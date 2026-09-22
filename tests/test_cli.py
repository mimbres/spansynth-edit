import json
import numpy as np
import pytest
import soundfile as sf
import torch
from mido import MidiFile, MidiTrack, Message
from spansynth.cli import build_parser, main, resolve_device


def test_requested_defaults():
    args = build_parser().parse_args(["edit", "--audio", "a.wav", "--midi", "a.mid", "--output", "out"])
    assert (args.steps, args.cfg, args.method, args.context_midi, args.drop_context_audio) == (16, 2, "ordinary", False, False)


@pytest.mark.parametrize("cuda,mps,expected", [(True, True, "cuda"), (False, True, "mps"), (False, False, "cpu")])
def test_auto_device_priority(monkeypatch, cuda, mps, expected):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda)
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: mps)
    assert str(resolve_device("auto")) == expected
    assert str(resolve_device("cpu")) == "cpu"


def test_explicit_device_selection(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
    assert str(resolve_device("mps")) == "mps"
    assert str(resolve_device("cuda:1")) == "cuda:1"
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
    with pytest.raises(ValueError, match="Apple GPU.*not available"):
        resolve_device("mps")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(ValueError, match="CUDA.*not available"):
        resolve_device("cuda")
    with pytest.raises(ValueError, match="Supported devices"):
        resolve_device("meta")


def test_preflight_resampling_stereo_timing_and_no_output(tmp_path, capsys):
    audio = tmp_path / "input.wav"
    sf.write(audio, np.zeros((44100, 2), dtype=np.float32), 44100)
    midi = MidiFile()
    midi.tracks.append(MidiTrack([Message("note_on", note=60, velocity=90), Message("note_off", note=60, time=480)]))
    path = tmp_path / "input.mid"
    midi.save(path)
    output = tmp_path / "out"
    args = ["synthesize", "--audio", str(audio), "--midi", str(path), "--output", str(output),
            "--edit-start", "0.11", "--edit-end", "0.91", "--check-inputs"]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied_interval_seconds"] == [0.08, 0.92]
    assert result["input_sample_rate"] == 44100
    assert result["input_channels"] == 2
    assert abs(result["padded_seconds"] - 19.48) < 1e-6
    assert not output.exists()


def test_invalid_input_fails_before_download(tmp_path, capsys):
    assert main(["edit", "--audio", "missing.wav", "--midi", "missing.mid", "--output", str(tmp_path),
                 "--method", "flowedit"]) == 1
    assert "requires --source-midi" in capsys.readouterr().err
