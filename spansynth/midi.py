# SPDX-FileCopyrightText: 2026 SpanSynth contributors
# SPDX-License-Identifier: Apache-2.0

"""Tempo-aware MIDI parsing and the checkpoint's 25 Hz event representation.

The parser and instrument vocabulary adapt YourMT3 (Apache-2.0).
"""
from __future__ import annotations
import math
import os
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal
import numpy as np
import torch
from torch import Tensor
from mido import MidiFile
from .vocabulary import program_category_class_index, program_category_count

ScaleProgramConditioningMode = Literal["fine40"]
SCALE_PROGRAM_CONDITIONING_MODES = ("fine40",)
def _require_plain_int(name: str, value: int, *, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be in [{minimum}, {maximum}], got {value}")


def _require_nonnegative_finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, got {type(value).__name__}")
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be finite and nonnegative, got {value}")


@dataclass(frozen=True, slots=True)
class MidiNote:
    """One melodic MIDI note with unquantized absolute times in seconds.

    The interval is half-open: the note is active on ``[onset, offset)``.
    Zero-duration notes are retained because their onset is still meaningful
    to the binary onset roll, even though they occupy no velocity-roll frame.
    """

    onset: float
    offset: float
    pitch: int
    velocity: int
    program: int
    channel: int

    def __post_init__(self) -> None:
        _require_nonnegative_finite("onset", self.onset)
        _require_nonnegative_finite("offset", self.offset)
        if self.offset < self.onset:
            raise ValueError(
                f"offset must be greater than or equal to onset, got {self.offset} < {self.onset}"
            )
        _require_plain_int("pitch", self.pitch, minimum=0, maximum=127)
        _require_plain_int("velocity", self.velocity, minimum=1, maximum=127)
        # 128 is YourMT3's internal drum program used by the Slakh source.
        _require_plain_int("program", self.program, minimum=0, maximum=128)
        _require_plain_int("channel", self.channel, minimum=0, maximum=15)

    @property
    def duration(self) -> float:
        """Return the unquantized duration in seconds."""

        return self.offset - self.onset


@dataclass(frozen=True, slots=True)
class TempoChange:
    """An explicit MIDI ``set_tempo`` event on the absolute time axis."""

    time_seconds: float
    microseconds_per_beat: int

    def __post_init__(self) -> None:
        _require_nonnegative_finite("time_seconds", self.time_seconds)
        _require_plain_int(
            "microseconds_per_beat",
            self.microseconds_per_beat,
            minimum=1,
            maximum=0xFFFFFF,
        )

    @property
    def beats_per_minute(self) -> float:
        """Return the tempo in beats per minute."""

        return 60_000_000.0 / self.microseconds_per_beat


@dataclass(frozen=True, slots=True)
class MidiParseResult:
    """Safe, immutable output of one complete MIDI parse."""

    notes: tuple[MidiNote, ...]
    duration_seconds: float
    ticks_per_beat: int
    tempo_changes: tuple[TempoChange, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.notes, tuple):
            raise TypeError("notes must be a tuple")
        if not all(isinstance(note, MidiNote) for note in self.notes):
            raise TypeError("notes must contain only MidiNote instances")
        _require_nonnegative_finite("duration_seconds", self.duration_seconds)
        _require_plain_int(
            "ticks_per_beat", self.ticks_per_beat, minimum=1, maximum=0x7FFF
        )
        if not isinstance(self.tempo_changes, tuple):
            raise TypeError("tempo_changes must be a tuple")
        if not all(isinstance(change, TempoChange) for change in self.tempo_changes):
            raise TypeError("tempo_changes must contain only TempoChange instances")

        if any(note.offset > self.duration_seconds for note in self.notes):
            raise ValueError("note offsets may not exceed duration_seconds")
        if any(change.time_seconds > self.duration_seconds for change in self.tempo_changes):
            raise ValueError("tempo changes may not exceed duration_seconds")

@dataclass(frozen=True, slots=True)
class _PendingNote:
    onset: float
    pitch: int
    velocity: int
    program: int
    channel: int


def _validate_optional_program(program: int | None) -> None:
    if program is None:
        return
    if isinstance(program, bool) or not isinstance(program, int):
        raise TypeError(f"program must be an integer or None, got {type(program).__name__}")
    if not 0 <= program <= 128:
        raise ValueError(f"program must be in [0, 128], got {program}")


def _validate_pitch_shift(pitch_shift: int) -> None:
    if isinstance(pitch_shift, bool) or not isinstance(pitch_shift, int):
        raise TypeError(
            f"pitch_shift must be an integer, got {type(pitch_shift).__name__}"
        )


def parse_midi_notes(
    path: os.PathLike[str] | str,
    *,
    program: int | None,
    pitch_shift: int = 0,
) -> MidiParseResult:
    """Read MIDI tempo, programs, repeated notes and sustain pedal events."""

    _validate_optional_program(program)
    _validate_pitch_shift(pitch_shift)

    midi = MidiFile(file=path) if hasattr(path, "read") else MidiFile(path)
    if midi.type == 2:
        raise ValueError("asynchronous type-2 MIDI files have no single absolute timeline")

    program_by_channel: list[int | None] = [0] * 16
    # General MIDI reserves channel 10 (zero-based channel 9) for drums. A
    # program-change event cannot encode our internal drum program 128, so
    # standards-compliant multi-track files commonly omit one on this channel.
    program_by_channel[9] = 128
    sustain_by_channel = [False] * 16
    active: dict[tuple[int, int], deque[_PendingNote]] = {}
    sustained: list[list[_PendingNote]] = [[] for _ in range(16)]
    finished: list[MidiNote] = []
    tempo_changes: list[TempoChange] = []

    current_time = 0.0

    def finish_note(note: _PendingNote, offset: float) -> None:
        # MIDI deltas cannot move backwards. Keep the explicit check here so a
        # malformed parser state never leaks into the public immutable object.
        if offset < note.onset:
            raise ValueError(f"note offset {offset} precedes onset {note.onset}")
        finished.append(
            MidiNote(
                onset=note.onset,
                offset=offset,
                pitch=note.pitch,
                velocity=note.velocity,
                program=note.program,
                channel=note.channel,
            )
        )

    for message in midi:
        current_time += float(message.time)
        if not math.isfinite(current_time) or current_time < 0.0:
            raise ValueError(f"invalid absolute MIDI event time {current_time}")

        if message.type == "set_tempo":
            tempo_changes.append(
                TempoChange(
                    time_seconds=current_time,
                    microseconds_per_beat=int(message.tempo),
                )
            )
            continue

        if message.type == "program_change":
            channel = int(message.channel)
            program_by_channel[channel] = (
                128 if channel == 9 else int(message.program)
            )
            continue

        if message.type == "control_change" and message.control == 64:
            channel = int(message.channel)
            if message.value >= 64:
                sustain_by_channel[channel] = True
            else:
                sustain_by_channel[channel] = False
                pending = sustained[channel]
                sustained[channel] = []
                for note in pending:
                    finish_note(note, current_time)
            continue

        is_note_on = message.type == "note_on" and message.velocity > 0
        is_note_off = message.type == "note_off" or (
            message.type == "note_on" and message.velocity == 0
        )
        if is_note_on:
            channel = int(message.channel)
            note_program = program if program is not None else program_by_channel[channel]
            if note_program is None:
                raise ValueError(
                    f"note_on on channel {channel} has no program; pass the source program"
                )
            pitch = int(message.note) + pitch_shift
            if not 0 <= pitch <= 127:
                raise ValueError(
                    f"pitch {message.note} with shift {pitch_shift} is outside [0, 127]"
                )
            key = (channel, int(message.note))
            active.setdefault(key, deque()).append(
                _PendingNote(
                    onset=current_time,
                    pitch=pitch,
                    velocity=int(message.velocity),
                    program=note_program,
                    channel=channel,
                )
            )
            continue

        if is_note_off:
            channel = int(message.channel)
            key = (channel, int(message.note))
            queue = active.get(key)
            if not queue:
                # Unmatched note-offs occur in real-world files. They do not
                # describe an audible note and are safe to ignore.
                continue
            note = queue.popleft()
            if not queue:
                del active[key]
            if note.program != 128 and sustain_by_channel[channel]:
                sustained[channel].append(note)
            else:
                finish_note(note, current_time)

    # Close hanging key-down and pedal-held notes at the true end of the merged
    # MIDI timeline. This preserves the source duration without inventing a
    # rounded 10 ms tail.
    for queue in active.values():
        for note in queue:
            finish_note(note, current_time)
    for channel_notes in sustained:
        for note in channel_notes:
            finish_note(note, current_time)

    finished.sort(
        key=lambda note: (
            note.onset,
            note.offset,
            note.program,
            note.pitch,
            note.velocity,
            note.channel,
        )
    )
    return MidiParseResult(
        notes=tuple(finished),
        duration_seconds=current_time,
        ticks_per_beat=int(midi.ticks_per_beat),
        tempo_changes=tuple(tempo_changes),
    )

SAMPLE_RATE: Final = 48_000


FRAME_RATE: Final = 25


HOP_LENGTH: Final = 1_920


FRAMES: Final = 512


DIM: Final = 128


CAPACITY: Final = 128


NUMERIC_WIDTH: Final = 7


KIND_PADDING: Final = 0


KIND_ONSET: Final = 1


KIND_SUSTAIN: Final = 2


KIND_OFFSET: Final = 3


IS_DRUM_INDEX: Final = 0


CATEGORY_SIN_INDEX: Final = 1


CATEGORY_COS_INDEX: Final = 2


PITCH_INDEX: Final = 3


VELOCITY_INDEX: Final = 4


BOUNDARY_INDEX: Final = 5


REMAINING_DURATION_INDEX: Final = 6


NOTE_DTYPE: Final = np.dtype(
    [
        ("onset", "<i4"),
        ("offset", "<i4"),
        ("pitch", "u1"),
        ("velocity", "u1"),
        ("program", "u1"),
        ("physical_id", "<u2"),
    ]
)


@dataclass(frozen=True, slots=True)
class MidiStats:
    """Capacity counters for one decoded note set."""

    emitted_rows: int
    maximum_rows_per_frame: int
    active_frames: int
    physical_notes: int
    capacity: int = CAPACITY

    @property
    def fits(self) -> bool:
        return self.maximum_rows_per_frame <= self.capacity

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "emitted_rows": self.emitted_rows,
            "maximum_rows_per_frame": self.maximum_rows_per_frame,
            "active_frames": self.active_frames,
            "physical_notes": self.physical_notes,
            "capacity": self.capacity,
            "fits": self.fits,
        }


class MidiOverflowError(ValueError):
    """Raised when a 25 Hz frame exceeds K128."""

    def __init__(self, stats: MidiStats) -> None:
        self.stats = stats
        super().__init__(
            "SpanSynth V3 SQ MIDI capacity exceeded: "
            f"max={stats.maximum_rows_per_frame}, capacity={stats.capacity}"
        )


@dataclass(frozen=True, slots=True)
class _ActiveRow:
    kind_id: int
    pitch: int
    velocity: int
    program: int
    category_id: int
    physical_id: int
    boundary_sample: int
    remaining_samples: int

    @property
    def is_drum(self) -> bool:
        return self.program == 128


def _note_value(note: np.void | Mapping[str, object], name: str) -> int:
    if isinstance(note, np.void):
        return int(note[name])
    return int(note[name])


def _expand_midi_rows(
    notes: np.ndarray | Sequence[Mapping[str, object]],
    valid_mask: Tensor,
    *,
    program_mode: ScaleProgramConditioningMode = "fine40",
) -> tuple[list[list[_ActiveRow]], MidiStats]:
    """Expand compact note intervals and return their exact K128 load."""

    if program_mode not in SCALE_PROGRAM_CONDITIONING_MODES:
        raise ValueError("unsupported MIDI program mode")
    if not isinstance(valid_mask, Tensor) or valid_mask.shape != (
        FRAMES,
    ):
        raise ValueError("valid_mask must have shape [512]")
    if valid_mask.dtype is not torch.bool:
        raise TypeError("valid_mask must use torch.bool")
    valid_cpu = valid_mask.detach().cpu()
    frame_rows: list[list[_ActiveRow]] = [
        [] for _ in range(FRAMES)
    ]
    horizon = FRAMES * HOP_LENGTH
    physical_ids: set[int] = set()
    for note in notes:
        onset = _note_value(note, "onset")
        offset = _note_value(note, "offset")
        pitch = _note_value(note, "pitch")
        velocity = _note_value(note, "velocity")
        program = _note_value(note, "program")
        physical_id = _note_value(note, "physical_id")
        if not 0 <= pitch <= 127:
            raise ValueError("note pitch must lie in [0, 127]")
        if not 1 <= velocity <= 127:
            raise ValueError("note velocity must lie in [1, 127]")
        if not 0 <= program <= 128:
            raise ValueError("note program must lie in [0, 128]")
        if physical_id < 0:
            raise ValueError("physical note IDs must be nonnegative")
        category = program_category_class_index(program, program_mode)
        physical_ids.add(physical_id)
        if program == 128:
            if 0 <= onset < horizon:
                frame = onset // HOP_LENGTH
                if bool(valid_cpu[frame].item()):
                    frame_rows[frame].append(
                        _ActiveRow(
                            kind_id=KIND_ONSET,
                            pitch=pitch,
                            velocity=velocity,
                            program=program,
                            category_id=category,
                            physical_id=physical_id,
                            boundary_sample=(
                                onset - frame * HOP_LENGTH
                            ),
                            remaining_samples=0,
                        )
                    )
            continue
        if offset <= onset:
            raise ValueError("melodic notes must have positive duration")
        first = max(0, onset // HOP_LENGTH)
        stop = min(
            FRAMES,
            -((-offset) // HOP_LENGTH),
        )
        for frame in range(first, stop):
            if not bool(valid_cpu[frame].item()):
                continue
            frame_start = frame * HOP_LENGTH
            frame_stop = frame_start + HOP_LENGTH
            if not (frame_start < offset and onset < frame_stop):
                continue
            if frame_start <= onset < frame_stop:
                kind = KIND_ONSET
                boundary = onset - frame_start
                remaining = offset - onset
            elif frame_start < offset <= frame_stop:
                kind = KIND_OFFSET
                boundary = offset - frame_start
                remaining = offset - frame_start
            else:
                kind = KIND_SUSTAIN
                boundary = 0
                remaining = offset - frame_start
            frame_rows[frame].append(
                _ActiveRow(
                    kind_id=kind,
                    pitch=pitch,
                    velocity=velocity,
                    program=program,
                    category_id=category,
                    physical_id=physical_id,
                    boundary_sample=boundary,
                    remaining_samples=remaining,
                )
            )
    maximum = max((len(rows) for rows in frame_rows), default=0)
    stats = MidiStats(
        emitted_rows=sum(len(rows) for rows in frame_rows),
        maximum_rows_per_frame=maximum,
        active_frames=sum(bool(rows) for rows in frame_rows),
        physical_notes=len(physical_ids),
    )
    return frame_rows, stats


def encode_midi(
    notes: np.ndarray | Sequence[Mapping[str, object]],
    valid_mask: Tensor,
    *,
    program_mode: ScaleProgramConditioningMode = "fine40",
) -> dict[str, Tensor | MidiStats]:
    """Expand compact note intervals into lossless framewise active rows."""

    frame_rows, stats = _expand_midi_rows(
        notes,
        valid_mask,
        program_mode=program_mode,
    )
    if not stats.fits:
        raise MidiOverflowError(stats)

    for rows in frame_rows:
        rows.sort(
            key=lambda row: (
                row.is_drum,
                row.category_id,
                row.pitch,
                row.kind_id,
                row.physical_id,
            )
        )

    slots = (FRAMES, CAPACITY)
    kind_id = torch.zeros(slots, dtype=torch.int64)
    pitch_id = torch.zeros(slots, dtype=torch.int64)
    numeric = torch.zeros(
        (*slots, NUMERIC_WIDTH), dtype=torch.float32
    )
    category_id = torch.full(slots, -1, dtype=torch.int64)
    physical_note_id = torch.full(slots, -1, dtype=torch.int64)
    event_valid = torch.zeros(slots, dtype=torch.bool)
    remaining = torch.zeros(slots, dtype=torch.int64)
    horizon = FRAMES * HOP_LENGTH
    category_total = program_category_count(program_mode)
    for frame, rows in enumerate(frame_rows):
        for slot, row in enumerate(rows):
            kind_id[frame, slot] = row.kind_id
            pitch_id[frame, slot] = row.pitch + 1
            category_id[frame, slot] = row.category_id
            physical_note_id[frame, slot] = row.physical_id
            event_valid[frame, slot] = True
            angle = 2.0 * math.pi * row.category_id / category_total
            numeric[frame, slot, IS_DRUM_INDEX] = (
                1.0 if row.is_drum else -1.0
            )
            numeric[frame, slot, CATEGORY_SIN_INDEX] = math.sin(angle)
            numeric[frame, slot, CATEGORY_COS_INDEX] = math.cos(angle)
            numeric[frame, slot, PITCH_INDEX] = 2.0 * row.pitch / 127.0 - 1.0
            numeric[frame, slot, VELOCITY_INDEX] = (
                2.0 * row.velocity / 127.0 - 1.0
            )
            numeric[frame, slot, BOUNDARY_INDEX] = (
                0.0
                if row.kind_id == KIND_SUSTAIN
                else 2.0
                * row.boundary_sample
                / HOP_LENGTH
                - 1.0
            )
            remaining[frame, slot] = row.remaining_samples
    if bool(event_valid.any().item()):
        encoded_duration = encode_remaining_duration(
            remaining[event_valid],
            horizon_samples=horizon,
            sample_rate=SAMPLE_RATE,
        )
        numeric[..., REMAINING_DURATION_INDEX][event_valid] = encoded_duration
    return {
        "kind_id": kind_id,
        "pitch_id": pitch_id,
        "numeric": numeric,
        "category_id": category_id,
        "physical_note_id": physical_note_id,
        "event_valid": event_valid,
        "midi_capacity_stats": stats,
    }

def encode_remaining_duration(remaining_samples, *, horizon_samples, sample_rate):
    clipped = remaining_samples.to(torch.float64).clamp(0, horizon_samples)
    return (2.0 * torch.log1p(clipped / sample_rate) / math.log1p(horizon_samples / sample_rate) - 1.0).float()



def read_notes(path: Path | None, *, crop_start: float, offset: float = 0.0,
               program: int | None = None) -> np.ndarray:
    """Map MIDI time to audio time: audio_seconds = midi_seconds + offset."""
    if path is None:
        return np.zeros(0, dtype=NOTE_DTYPE)
    parsed = parse_midi_notes(path, program=program)
    origin = math.floor((crop_start - offset) * SAMPLE_RATE + 0.5)
    notes = []
    for note in parsed.notes:
        onset = math.floor(note.onset * SAMPLE_RATE + 0.5) - origin
        end = math.floor(note.offset * SAMPLE_RATE + 0.5) - origin
        if note.program != 128 and end == onset:
            end += 1
        if onset >= FRAMES * HOP_LENGTH or (end <= 0 and note.program != 128):
            continue
        if note.program == 128 and onset < 0:
            continue
        notes.append((onset, end, note.program, note.pitch, note.velocity))
    notes.sort()
    if len(notes) > np.iinfo(np.uint16).max:
        raise ValueError("The selected crop contains too many MIDI notes")
    result = np.zeros(len(notes), dtype=NOTE_DTYPE)
    for index, (onset, end, program, pitch, velocity) in enumerate(notes):
        result[index] = (onset, end, pitch, velocity, program, index)
    return result


def prepare_midi(target, source, generated, *, context_midi=False):
    """Use source MIDI in context and target MIDI inside the generated interval."""
    # Match whole crossing notes so CFG removes every fragment of one event.
    identities = defaultdict(deque)
    fields = ("onset", "offset", "pitch", "velocity", "program")
    source = source.copy()
    target = target.copy()
    for note in source:
        identities[tuple(int(note[k]) for k in fields)].append(int(note["physical_id"]))
    next_id = len(source)
    for note in target:
        matches = identities[tuple(int(note[k]) for k in fields)]
        if matches:
            note["physical_id"] = matches.popleft()
        else:
            if next_id > np.iinfo(np.uint16).max:
                raise ValueError("The selected crop contains too many MIDI notes")
            note["physical_id"] = next_id
            next_id += 1
    context = ~generated if context_midi else torch.zeros_like(generated)
    left = encode_midi(source, context)
    right = encode_midi(target, generated)
    merged = {}
    for key in ("kind_id", "pitch_id", "numeric", "category_id", "physical_note_id", "event_valid"):
        mask = generated[:, None, None] if key == "numeric" else generated[:, None]
        merged[key] = torch.where(mask, right[key], left[key]).unsqueeze(0)
    return merged
