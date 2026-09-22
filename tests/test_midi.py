import numpy as np
import pytest
import torch
from mido import MidiFile, MidiTrack, Message, MetaMessage
from spansynth.midi import read_notes, prepare_midi, encode_midi, NOTE_DTYPE
from spansynth.sampling import _without_generated_notes


def test_tempo_sustain_and_crop_alignment(tmp_path):
    midi = MidiFile(ticks_per_beat=480)
    track = MidiTrack()
    midi.tracks.append(track)
    track.extend([MetaMessage("set_tempo", tempo=500000), Message("note_on", note=60, velocity=90),
                  Message("control_change", control=64, value=127, time=240),
                  Message("note_off", note=60, time=240), MetaMessage("set_tempo", tempo=1000000),
                  Message("control_change", control=64, value=0, time=480)])
    path = tmp_path / "tempo.mid"
    midi.save(path)
    notes = read_notes(path, crop_start=1, offset=0.5)
    assert notes[0]["onset"] == -24000
    assert notes[0]["offset"] == 48000
    assert notes[0]["program"] == 0


def test_crossing_note_identity_and_context_dropout():
    notes = np.array([(0, 48000, 60, 90, 0, 0)], dtype=NOTE_DTYPE)
    generated = torch.zeros(512, dtype=torch.bool)
    generated[10:20] = True
    full = prepare_midi(notes, notes, generated, context_midi=True)
    assert full["event_valid"][0, :25].any(dim=1).all()
    off = _without_generated_notes(full, generated[None])
    assert not off["event_valid"].any()
    default = prepare_midi(notes, notes, generated)
    assert not default["event_valid"][0, ~generated].any()


def test_drum_onset_and_capacity():
    notes = np.array([(0, 48000, 36, 90, 128, 0)], dtype=NOTE_DTYPE)
    rows = encode_midi(notes, torch.ones(512, dtype=torch.bool))
    assert rows["event_valid"].sum() == 1
    crowded = np.array([(0, 100, 60, 90, 0, i) for i in range(129)], dtype=NOTE_DTYPE)
    with pytest.raises(ValueError, match="capacity exceeded"):
        encode_midi(crowded, torch.ones(512, dtype=torch.bool))


def test_unsupported_instrument_fails_clearly():
    notes = np.array([(0, 48000, 60, 90, 127, 0)], dtype=NOTE_DTYPE)
    with pytest.raises(ValueError, match="Fine40"):
        encode_midi(notes, torch.ones(512, dtype=torch.bool))
