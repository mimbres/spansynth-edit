"""Run against a real ComfyUI checkout supplied through PYTHONPATH."""
import io
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import torch

pytest.importorskip("comfy_api")
from comfy.cli_args import args
args.cpu = True
from comfy.model_patcher import ModelPatcher
import folder_paths
from spansynth import comfyui as nodes
from spansynth.audio import read_audio
from spansynth.codec.config import ScalarModelConfig
from spansynth.codec.sq_codec import ScalarModel
from spansynth.midi import read_notes
from spansynth.model import DiTConfig, EventSetConfig, SpanSynth


@pytest.fixture(autouse=True)
def temporary_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(folder_paths, "temp_directory", str(tmp_path))
    monkeypatch.setattr(folder_paths, "input_directory", str(tmp_path))
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


def note(**changes):
    return dict({"start": .1, "duration": .4, "pitch": 60, "velocity": 90, "program": 16}, **changes)


def clip():
    values = torch.rand(1, 2, 24000) * .1
    return nodes.SpanSynthPrepareClip.execute({"waveform": values, "sample_rate": 24000}, .2, .8)[0]


def test_example_loads_aligned_audio_and_midi_without_transcription(tmp_path):
    workflow = nodes.prepare_example()
    by_type = {n["type"]: n for n in workflow["nodes"]}
    assert "SpanSynthTranscribe" not in by_type
    audio_name = by_type["LoadAudio"]["widgets_values"][0]
    midi_name, offset = by_type["SpanSynthLoadMidi"]["widgets_values"]
    samples, rate = sf.read(tmp_path / audio_name, dtype="float32", always_2d=True)
    prepared = nodes.SpanSynthPrepareClip.execute(
        {"waveform": torch.from_numpy(samples.T.copy())[None], "sample_rate": rate})[0]
    midi = nodes.SpanSynthLoadMidi.execute(midi_name, offset)[0]
    result = nodes.SpanSynthEditScore.execute(prepared, midi)
    score = result.ui["score"][0]
    assert score["notes"] and score["notes"] == score["source"]
    assert all(0 <= n["start"] < n["start"] + n["duration"] <= score["duration"] + 1e-6
               for n in score["notes"])
    assert result[1:3] == (0, score["duration"])
    assert all(link[1] in {n["id"] for n in workflow["nodes"]} and
               link[3] in {n["id"] for n in workflow["nodes"]} for link in workflow["links"])
    before = {p.name: p.stat().st_mtime_ns for p in tmp_path.glob("early-kraisler-*")}
    nodes.prepare_example()
    assert before == {p.name: p.stat().st_mtime_ns for p in tmp_path.glob("early-kraisler-*")}


def test_example_downloads_only_missing_inputs_in_sparse_install(tmp_path, monkeypatch):
    from urllib import request
    original_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda p: False if "demo/assets" in str(p) else original_is_file(p))
    source = Path(nodes.__file__).resolve().parents[1] / "demo/assets"
    requested = []
    def download(url, timeout):
        name = url.rsplit("/", 1)[-1]
        requested.append(name)
        return io.BytesIO((source / name).read_bytes())
    monkeypatch.setattr(request, "urlopen", download)
    nodes.prepare_example()
    assert len(requested) == 2
    (tmp_path / "early-kraisler-track01-before.mid").write_bytes(b"User-owned replacement")
    nodes.prepare_example()
    assert len(requested) == 2
    assert (tmp_path / "early-kraisler-track01-before.mid").read_bytes() == b"User-owned replacement"


def test_audio_matches_file_crop_and_preserves_history(tmp_path):
    values = np.random.default_rng().uniform(-.1, .1, (48000, 2)).astype(np.float32)
    path = tmp_path / "input.wav"
    sf.write(path, values, 24000, subtype="FLOAT")
    expected = read_audio(path, .3, 1.2)
    actual = nodes.SpanSynthPrepareClip.execute({"waveform": torch.from_numpy(values.T)[None], "sample_rate": 24000}, .3, 1.2)[0]["crop"]
    np.testing.assert_array_equal(actual.original, expected.original)
    torch.testing.assert_close(actual.waveform, expected.waveform, rtol=0, atol=0)
    with pytest.raises(ValueError, match="batch size"):
        nodes.SpanSynthPrepareClip.execute({"waveform": torch.zeros(2, 1, 24000), "sample_rate": 24000})


def test_midi_roundtrip_sustain_drums_and_workflow_reload():
    prepared = clip()
    source = {"data": nodes.midi_bytes([note(start=.1), note(program=128, start=.1)]), "origin": 0}
    first = nodes.SpanSynthEditScore.execute(prepared, source)
    data = first.ui["score"][0]
    assert len(data["notes"]) == 1
    assert data["notes"][0]["source_onset"] == pytest.approx(-.1)
    expected = nodes.score_notes(source, .2)
    np.testing.assert_array_equal(nodes.score_notes(first[0], .2), expected)
    data["notes"][0]["pitch"] += 5
    restored = nodes.SpanSynthEditScore.execute(prepared, source, json.dumps(data))
    assert restored[0]["notes"][0]["pitch"] == 65
    assert restored[1] == 0 and restored[2] == pytest.approx(.32)
    exported = read_notes(io.BytesIO(restored[0]["data"]), crop_start=0)
    assert exported[0]["pitch"] == 65
    with pytest.raises(ValueError, match="original MIDI changed"):
        nodes.SpanSynthEditScore.execute(prepared, None, json.dumps(data))


def test_deleted_and_moved_notes_expand_region():
    source = [note(start=.15, duration=.4), note(start=.9, duration=.12, pitch=67)]
    changed = [note(start=.4, duration=.4, pitch=64)]
    assert nodes.changed_region(source, changed, 1.2) == pytest.approx((.12, 1.04))
    assert nodes.changed_region(source, source, 1.2) == (0, 1.2)


def test_midi_export_keeps_submillisecond_transcription_timing():
    source = [note(start=1900 / 48000, duration=15400 / 48000)]
    parsed = read_notes(io.BytesIO(nodes.midi_bytes(source)), crop_start=0)
    assert parsed[0]["onset"] == 1900
    assert parsed[0]["offset"] == 17300


def test_invalid_midi_paths_and_scores(tmp_path):
    with pytest.raises(ValueError, match="inside"):
        nodes.SpanSynthLoadMidi.execute("../private.mid")
    with pytest.raises(ValueError, match="duration"):
        nodes.validate_notes([note(duration=float("nan"))], 1)
    with pytest.raises(ValueError, match="15 melodic"):
        nodes.validate_notes([note(program=p) for p in range(16)], 1)


@pytest.fixture
def models():
    device = torch.device("cpu")
    transformer = SpanSynth(DiTConfig(hidden_size=32, depth=1, num_heads=4, conv_pos_groups=4), EventSetConfig()).eval()
    codec = ScalarModel(ScalarModelConfig(init_channel=2)).eval()
    return ModelPatcher(transformer, device, device), ModelPatcher(codec, device, device)


@pytest.mark.parametrize("method", ["spansynth-edit", "spansynth-edit + flowedit"])
def test_real_generation_keeps_original_context_and_can_repeat(models, method):
    prepared = clip()
    original = prepared["crop"].original.copy()
    source = {"data": nodes.midi_bytes([note()]), "origin": .2}
    score = {"data": nodes.midi_bytes([note(pitch=65)]), "origin": .2}
    first = nodes.SpanSynthGenerate.execute(models, prepared, score, method, .22, .57, 2, 2, source)
    samples = first[0]["waveform"][0, 0].numpy()
    np.testing.assert_array_equal(samples[:9600], original[:9600])
    np.testing.assert_array_equal(samples[28800:], original[28800:])
    assert np.isfinite(samples).all()
    assert first[1]["waveform"].shape[-1] == 19200
    second = nodes.SpanSynthGenerate.execute(models, prepared, source, method, .22, .57, 2, 2, source)
    assert torch.isfinite(second[0]["waveform"]).all()
    np.testing.assert_array_equal(prepared["crop"].original, original)
    assert np.isnan(nodes.SpanSynthGenerate.fingerprint_inputs())


def test_synthesis_without_input_audio(models):
    prepared = nodes.SpanSynthPrepareClip.execute(duration=.6)[0]
    score = {"data": nodes.midi_bytes([note()]), "origin": 0}
    result = nodes.SpanSynthGenerate.execute(models, prepared, score, region_start=0, region_end=.6, steps=1)
    assert result[0]["waveform"].shape == (1, 1, 28800)
    assert torch.isfinite(result[0]["waveform"]).all()


def test_transcription_passes_auth_and_crop_origin(monkeypatch):
    from concurrent.futures import Future
    import gradio_client
    import huggingface_hub
    import base64
    payload = nodes.midi_bytes([note()])
    received = {}
    class Client:
        def __init__(self, target, **kwargs):
            received.update(target=target, **kwargs)
        def submit(self, path, **kwargs):
            received.update(kwargs)
            result = Future()
            result.set_result('data:audio/midi;base64,' + base64.b64encode(payload).decode())
            return result
        def close(self):
            received["closed"] = True
    monkeypatch.setattr(gradio_client, "Client", Client)
    monkeypatch.setattr(huggingface_hub, "get_token", lambda: "test-login")
    result = nodes.SpanSynthTranscribe.execute(clip())[0]
    assert result == {"data": payload, "origin": .2}
    assert received["token"] == "test-login" and received["closed"]
    assert received["api_name"] == "/process_audio"


def test_cancel_also_stops_pending_transcription(monkeypatch):
    from concurrent.futures import Future
    import gradio_client
    job = Future()
    closed = []
    class Client:
        def __init__(self, *args, **kwargs):
            pass
        def submit(self, *args, **kwargs):
            nodes.memory.interrupt_current_processing(True)
            return job
        def close(self):
            closed.append(True)
    monkeypatch.setattr(gradio_client, "Client", Client)
    try:
        with pytest.raises(nodes.memory.InterruptProcessingException):
            nodes.SpanSynthTranscribe.execute(clip())
    finally:
        nodes.memory.interrupt_current_processing(False)
    assert job.cancelled() and closed
