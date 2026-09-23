"""Exercise real small components through Diffusers loading and the public CLI."""

from dataclasses import asdict
import json
from pathlib import Path
from unittest.mock import patch

import mido
import numpy as np
import pytest
from safetensors.torch import save_file
import soundfile as sf
import torch

pytest.importorskip("diffusers")
from diffusers import DiffusionPipeline

from scripts.export_checkpoint import export_diffusers
from spansynth.cli import build_parser, run
from spansynth.codec.config import ScalarModelConfig
from spansynth.codec.sq_codec import ScalarModel
from spansynth.diffusers_models import HeartCodecModel, SpanSynthTransformerModel
from spansynth.diffusers_pipeline import SpanSynthEditPipeline
from spansynth.model import DiTConfig, EventSetConfig, SpanSynth


@pytest.fixture(scope="module", autouse=True)
def cpu_threads():
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


@pytest.fixture
def reference(tmp_path):
    dit = DiTConfig(hidden_size=32, depth=1, num_heads=4, conv_pos_groups=4)
    events = EventSetConfig()
    transformer = SpanSynth(dit, events).eval()
    codec = ScalarModel(ScalarModelConfig(init_channel=2)).eval()
    model_dir, codec_dir = tmp_path / "original-model", tmp_path / "original-codec"
    model_dir.mkdir()
    codec_dir.mkdir()
    config = {"format_version": 1, "architecture": "spansynth_v8", "step": 127750,
              "sample_rate": 48000, "frames": 512, "hop_length": 1920,
              "dit": asdict(dit), "event_set": asdict(events)}
    (model_dir / "config.json").write_text(json.dumps(config))
    save_file(transformer.state_dict(), model_dir / "model.safetensors")
    (codec_dir / "scalar_model_config.json").write_text(json.dumps(codec.config.to_dict()))
    save_file(codec.state_dict(), codec_dir / "scalar_model.safetensors")
    return transformer, codec, model_dir, codec_dir


def test_component_conversion_and_roundtrip(reference, tmp_path):
    transformer, codec, model_dir, codec_dir = reference
    destination = export_diffusers(model_dir, codec_dir, tmp_path / "converted")
    restored = SpanSynthEditPipeline.from_pretrained(destination)
    assert set(restored.transformer.state_dict()) == set(transformer.state_dict())
    for name, value in transformer.state_dict().items():
        torch.testing.assert_close(restored.transformer.state_dict()[name], value, rtol=0, atol=0)
    for name, value in codec.state_dict().items():
        torch.testing.assert_close(restored.codec.model.state_dict()[name], value, rtol=0, atol=0)

    state = torch.randn(1, 8, 128)
    time = torch.rand(1)
    observed = torch.rand(1, 8) > 0.5
    midi = torch.randn(1, 8, 768)
    clean = torch.randn(1, 8, 32)
    inputs = (state, time, observed, midi, observed, clean)
    with torch.inference_mode():
        expected = transformer(*inputs)
        actual = restored.transformer(*inputs)
        torch.testing.assert_close(actual, expected, rtol=0, atol=1e-6)
        waveform = torch.randn(1, 1, 1920 * 6)
        torch.testing.assert_close(restored.codec(waveform), codec(waveform), rtol=0, atol=1e-6)

    restored.save_pretrained(tmp_path / "saved-again")
    again = SpanSynthEditPipeline.from_pretrained(tmp_path / "saved-again").to("cpu")
    with torch.inference_mode():
        torch.testing.assert_close(again.transformer(*inputs), expected, rtol=0, atol=1e-6)
    assert not again.transformer.blocks[0].self_attention.rope.frequency_indices.is_meta
    with pytest.raises(FileExistsError, match="already exists"):
        export_diffusers(model_dir, codec_dir, destination)


def test_community_pipeline_loading(reference, tmp_path, monkeypatch):
    from diffusers.utils import dynamic_modules_utils

    monkeypatch.setattr(dynamic_modules_utils, "HF_MODULES_CACHE", str(tmp_path / "modules"))
    _, _, model_dir, codec_dir = reference
    destination = export_diffusers(model_dir, codec_dir, tmp_path / "converted")
    code = Path(__file__).resolve().parents[1] / "spansynth" / "diffusers_pipeline.py"
    restored = DiffusionPipeline.from_pretrained(destination, custom_pipeline=str(code), trust_remote_code=True)
    assert isinstance(restored.transformer, SpanSynthTransformerModel)
    assert isinstance(restored.codec, HeartCodecModel)
    assert set(restored.components) == {"transformer", "codec"}


@pytest.mark.parametrize("command,method,context_midi,drop_context_audio,average", [
    ("synthesize", "ordinary", False, False, 1),
    ("edit", "ordinary", True, True, 1),
    ("edit", "flowedit", False, False, 2),
])
def test_pipeline_matches_cli(reference, tmp_path, command, method, context_midi, drop_context_audio, average):
    _, _, model_dir, codec_dir = reference
    destination = export_diffusers(model_dir, codec_dir, tmp_path / "converted")
    pipeline = SpanSynthEditPipeline.from_pretrained(destination)
    pipeline.set_progress_bar_config(disable=True)
    audio = tmp_path / "stereo.wav"
    original = np.random.default_rng().uniform(-0.2, 0.2, (33600, 2)).astype(np.float32)
    sf.write(audio, original, 24000, subtype="FLOAT")
    paths = []
    for name, pitch in (("source.mid", 60), ("target.mid", 65)):
        path = tmp_path / name
        midi = mido.MidiFile()
        midi.tracks.append(mido.MidiTrack([
            mido.Message("program_change", program=16),
            mido.Message("note_on", note=pitch, velocity=90, time=480),
            mido.Message("note_off", note=pitch, time=960),
        ]))
        midi.save(path)
        paths.append(path)
    source, target = paths
    args = [command, "--audio", str(audio), "--midi", str(target), "--source-midi", str(source),
            "--crop-start", "0.2", "--duration", "1.13", "--edit-start", "0.23", "--edit-end", "0.891",
            "--midi-offset", "0.15", "--source-midi-offset", "-0.05", "--steps", "2", "--cfg", "2",
            "--device", "cpu", "--threads", "2", "--checkpoint", str(model_dir), "--codec-dir", str(codec_dir),
            "--output", str(tmp_path / "cli")]
    if command == "edit":
        args += ["--method", method, "--average", str(average)]
    if context_midi:
        args.append("--context-midi")
    if drop_context_audio:
        args.append("--drop-context-audio")

    # Share freshly sampled tensors across the two paths without selecting a seed.
    noises = [torch.randn(average, 512, 128) for _ in range(2 if method == "flowedit" else 1)]
    with patch("torch.randn_like", side_effect=[value.clone() for value in noises]):
        assert run(build_parser().parse_args(args)) == 0
    with patch("torch.randn_like", side_effect=[value.clone() for value in noises]):
        result = pipeline(audio, target, source_midi=source, method=method,
                          crop_start=0.2, duration=1.13, edit_start=0.23, edit_end=0.891,
                          midi_offset=0.15, source_midi_offset=-0.05, num_inference_steps=2,
                          guidance_scale=2, context_midi=context_midi, drop_context_audio=drop_context_audio,
                          average=average, output_type="pt")
    expected, rate = sf.read(tmp_path / "cli" / "output.wav", dtype="float32")
    input_audio, _ = sf.read(tmp_path / "cli" / "input.wav", dtype="float32")
    assert result.audios.shape == (1, 1, len(expected))
    assert result.sample_rate == rate == 48000
    assert result.edit_interval == (0.2, 0.92)
    np.testing.assert_array_equal(result.audios.numpy()[0, 0], expected)
    np.testing.assert_array_equal(result.audios.numpy()[0, 0, :9600], input_audio[:9600])
    np.testing.assert_array_equal(result.audios.numpy()[0, 0, 44160:], input_audio[44160:])


@pytest.mark.parametrize("options,error", [
    ({"method": "flowedit"}, "source_midi"),
    ({"context_midi": True}, "source_midi"),
    ({"duration": 30}, "duration"),
    ({"guidance_scale": float("nan")}, "guidance_scale"),
    ({"num_inference_steps": 1.5}, "positive integer"),
    ({"start_step": 1}, "require FlowEdit"),
    ({"edit_end": 21}, "edit_end"),
])
def test_invalid_inputs_fail_before_model_execution(reference, options, error):
    model, codec, _, _ = reference
    pipeline = SpanSynthEditPipeline(model, codec)
    with pytest.raises(ValueError, match=error):
        pipeline("unused.wav", "unused.mid", **options)
