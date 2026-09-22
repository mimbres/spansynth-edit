from dataclasses import asdict
import json
import pytest
import torch
from safetensors.torch import save_file
from spansynth.model import SpanSynth, DiTConfig, EventSetConfig
from spansynth.checkpoint import load_model


def test_public_checkpoint_strict_roundtrip(tmp_path):
    dit = DiTConfig(hidden_size=32, depth=1, num_heads=4, conv_pos_groups=4)
    event = EventSetConfig()
    model = SpanSynth(dit, event).eval()
    config = {"format_version": 1, "architecture": "spansynth_v8", "step": 127750,
              "sample_rate": 48000, "frames": 512, "hop_length": 1920,
              "dit": asdict(dit), "event_set": asdict(event)}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    weights = tmp_path / "model.safetensors"
    save_file(model.state_dict(), str(weights))
    loaded, _ = load_model(path, weights)
    for name, value in model.state_dict().items():
        assert torch.equal(value, loaded.state_dict()[name])
    assert loaded.blocks[0].self_attention.rope.frequency_indices.device.type == "cpu"
    state = model.state_dict()
    state.pop("input_projection.bias")
    save_file(state, str(weights))
    with pytest.raises(RuntimeError, match="Missing key"):
        load_model(path, weights)
