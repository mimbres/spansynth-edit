"""MIDI-guided music synthesis and editing through Diffusers."""

from dataclasses import dataclass
import json
import math
from pathlib import Path

from diffusers import DiffusionPipeline
from diffusers.utils import BaseOutput
import numpy as np
import torch

from spansynth.audio import (
    FRAMES,
    HISTORY_FRAMES,
    HOP_LENGTH,
    SAMPLE_RATE,
    assemble_output,
    decode_audio,
    encode_audio,
    read_audio,
)
from spansynth.diffusers_models import HeartCodecModel, SpanSynthTransformerModel
from spansynth.midi import prepare_midi, read_notes
from spansynth.sampling import sample


@dataclass
class SpanSynthPipelineOutput(BaseOutput):
    """A batch of mono crops, their sample rate, and the applied editing interval.

    `audios` has shape `(1, 1, samples)` and float32 values. `edit_interval` gives
    the effective start and end in seconds relative to the crop.
    """

    audios: np.ndarray | torch.Tensor
    sample_rate: int
    edit_interval: tuple[float, float]


class SpanSynthEditPipeline(DiffusionPipeline):
    """Synthesise a MIDI score or edit a recording with the released SpanSynth model.

    Args:
        transformer (`SpanSynthTransformerModel`):
            Velocity model, including the MIDI encoder.
        codec (`HeartCodecModel`):
            Frozen Base SQ audio encoder and decoder.
    """

    model_cpu_offload_seq = "transformer->codec"

    def __init__(self, transformer: SpanSynthTransformerModel, codec: HeartCodecModel):
        super().__init__()
        self.register_modules(transformer=transformer, codec=codec)

    def to_json_string(self):
        """Identify the saved custom pipeline for Diffusers' standard loader."""
        config = json.loads(super().to_json_string())
        config["_class_name"] = ["pipeline", self.__class__.__name__]
        return json.dumps(config, indent=2, sort_keys=True) + "\n"

    def save_pretrained(self, save_directory: str | Path, **kwargs):
        """Save weights and the code entries required by the Hub's custom loader.

        The component entries import the installed `spansynth` package. All
        standard Diffusers saving options are forwarded, including Hub uploads.
        """
        destination = Path(save_directory)
        destination.mkdir(parents=True, exist_ok=True)
        source = Path(__file__).resolve()
        if source != (destination / "pipeline.py").resolve():
            (destination / "pipeline.py").write_text(source.read_text())
        for name, class_name in (("transformer", "SpanSynthTransformerModel"), ("codec", "HeartCodecModel")):
            component = destination / name
            component.mkdir(exist_ok=True)
            (component / "spansynth.diffusers_models.py").write_text(
                f"from spansynth.diffusers_models import {class_name}\n"
            )
        return super().save_pretrained(str(destination), **kwargs)

    @torch.inference_mode()
    def __call__(
        self,
        audio: str | Path,
        midi: str | Path,
        *,
        source_midi: str | Path | None = None,
        method: str = "ordinary",
        crop_start: float = 0.0,
        duration: float = 20.48,
        edit_start: float = 6.4,
        edit_end: float = 14.08,
        midi_offset: float = 0.0,
        source_midi_offset: float = 0.0,
        num_inference_steps: int = 16,
        guidance_scale: float = 2.0,
        context_midi: bool = False,
        drop_context_audio: bool = False,
        start_step: int = 0,
        average: int = 1,
        output_type: str = "np",
        return_dict: bool = True,
    ):
        """Generate one audio crop, preserving samples outside the selected region.

        Args:
            audio (`str` or `Path`):
                Recording providing the original crop and surrounding audio context.
            midi (`str` or `Path`):
                Full target score, including unchanged notes inside the edited region.
            source_midi (`str` or `Path`, *optional*):
                Original score, required by FlowEdit and context MIDI conditioning.
            method (`str`, defaults to `"ordinary"`):
                `"ordinary"` selects spansynth-edit; `"flowedit"` adds FlowEdit.
            crop_start (`float`, defaults to `0.0`):
                Crop origin on the recording's timeline, in seconds.
            duration (`float`, defaults to `20.48`):
                Output crop length, in seconds, at most 20.48.
            edit_start (`float`, defaults to `6.4`):
                Generated region's start in seconds relative to the crop.
            edit_end (`float`, defaults to `14.08`):
                Generated region's end in seconds relative to the crop.
            midi_offset (`float`, defaults to `0.0`):
                Seconds added to target MIDI times to align them with the recording.
            source_midi_offset (`float`, defaults to `0.0`):
                Independent alignment offset for the original MIDI.
            num_inference_steps (`int`, defaults to `16`):
                Number of Euler integration steps.
            guidance_scale (`float`, defaults to `2.0`):
                Nonnegative MIDI classifier-free guidance scale.
            context_midi (`bool`, defaults to `False`):
                Condition on source MIDI outside the generated region.
            drop_context_audio (`bool`, defaults to `False`):
                Omit clean audio conditioning while preserving unedited output samples.
            start_step (`int`, defaults to `0`):
                First integration step for FlowEdit only.
            average (`int`, defaults to `1`):
                Number of noise samples averaged per FlowEdit step.
            output_type (`str`, defaults to `"np"`):
                Return a NumPy array (`"np"`) or a CPU tensor (`"pt"`).
            return_dict (`bool`, defaults to `True`):
                Return a structured output, or its values as a tuple when false.

        Returns:
            `SpanSynthPipelineOutput` or `tuple`:
                Audio, sample rate, and effective editing interval. Boundaries are
                rounded outward to the codec's 40 ms grid and clipped to the crop.
        """
        if method not in ("ordinary", "flowedit"):
            raise ValueError("method must be ordinary or flowedit")
        times = (crop_start, duration, edit_start, edit_end, midi_offset, source_midi_offset)
        if not all(math.isfinite(value) for value in times):
            raise ValueError("Audio and MIDI times must be finite")
        if crop_start < 0 or not 0 < duration <= FRAMES * HOP_LENGTH / SAMPLE_RATE:
            raise ValueError("crop_start must be nonnegative and duration must lie in (0,20.48]")
        if not 0 <= edit_start < edit_end <= duration:
            raise ValueError("Require 0 <= edit_start < edit_end <= duration")
        if not math.isfinite(guidance_scale) or guidance_scale < 0:
            raise ValueError("guidance_scale must be finite and nonnegative")
        for name, value in (("num_inference_steps", num_inference_steps), ("average", average)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(start_step, bool) or not isinstance(start_step, int) or not 0 <= start_step < num_inference_steps:
            raise ValueError("start_step must be an integer in [0,num_inference_steps)")
        if method == "ordinary" and (start_step != 0 or average != 1):
            raise ValueError("start_step and average require FlowEdit")
        if (method == "flowedit" or context_midi) and source_midi is None:
            raise ValueError("FlowEdit and context_midi require source_midi")
        if output_type not in ("np", "pt"):
            raise ValueError("output_type must be np or pt")
        for path in (audio, midi, source_midi):
            if path is not None and not Path(path).is_file():
                raise FileNotFoundError(f"Input file does not exist: {path}")

        crop = read_audio(Path(audio), crop_start, duration)
        first = math.floor(edit_start * SAMPLE_RATE / HOP_LENGTH + 1e-9)
        last = math.ceil(edit_end * SAMPLE_RATE / HOP_LENGTH - 1e-9)
        first_sample = first * HOP_LENGTH
        last_sample = min(last * HOP_LENGTH, len(crop.original))
        if first_sample >= last_sample:
            raise ValueError("The selected interval contains no output samples")
        generated = torch.zeros(FRAMES, dtype=torch.bool)
        generated[first:last] = True
        target = read_notes(Path(midi), crop_start=crop_start, offset=midi_offset)
        need_source = context_midi or method == "flowedit"
        source = read_notes(Path(source_midi) if need_source else None,
                            crop_start=crop_start, offset=source_midi_offset)
        target_rows = prepare_midi(target, source, generated, context_midi=context_midi)
        source_rows = prepare_midi(source, source, generated, context_midi=context_midi) if method == "flowedit" else None

        device = self._execution_device
        encoded = encode_audio(self.codec, crop, device)
        history, codes = encoded[:, :HISTORY_FRAMES], encoded[:, HISTORY_FRAMES:]
        target_rows = {key: value.to(device) for key, value in target_rows.items()}
        if source_rows is not None:
            source_rows = {key: value.to(device) for key, value in source_rows.items()}
        steps = num_inference_steps - start_step
        with self.progress_bar(total=steps) as progress:
            with torch.autocast(device_type=device.type, dtype=torch.bfloat16, enabled=device.type == "cuda"):
                codes = sample(
                    self.transformer, codes, target_rows, (~generated)[None].to(device),
                    source_rows=source_rows, method=method, steps=num_inference_steps, cfg=guidance_scale,
                    context_midi=context_midi, drop_context_audio=drop_context_audio,
                    start_step=start_step, average=average, progress=lambda step, total: progress.update(),
                )
        decoded = decode_audio(self.codec, codes, history, device)
        result = assemble_output(crop.original, decoded, crop.gain, first_sample, last_sample)[None, None]
        if output_type == "pt":
            result = torch.from_numpy(result)
        self.maybe_free_model_hooks()
        output = SpanSynthPipelineOutput(result, SAMPLE_RATE, (first_sample / SAMPLE_RATE, last_sample / SAMPLE_RATE))
        return output if return_dict else output.to_tuple()
