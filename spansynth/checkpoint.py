"""Download and strictly load the public V8 model and its frozen Base SQ codec."""
from __future__ import annotations
import json
from pathlib import Path
import torch
from safetensors.torch import load_file
from .model import SpanSynth, DiTConfig, EventSetConfig

REPO_ID = "mimbres/spansynth-edit"
MODEL_FOLDER = "v3-sq-v8-127750step"
CODEC_FOLDER = "heartcodec-sq"


def resolve_assets(checkpoint=None, codec_dir=None, *, cache_dir=None, offline=False, revision="main"):
    from huggingface_hub import hf_hub_download
    def resolve(directory, folder, names):
        if directory is not None:
            paths = [Path(directory).expanduser() / name for name in names]
            for path in paths:
                if not path.is_file():
                    raise FileNotFoundError(f"Required checkpoint file is missing: {path}")
            return paths
        return [Path(hf_hub_download(REPO_ID, f"{folder}/{name}", revision=revision,
                                    cache_dir=cache_dir, local_files_only=offline)) for name in names]
    model = resolve(checkpoint, MODEL_FOLDER, ("config.json", "model.safetensors"))
    codec = resolve(codec_dir, CODEC_FOLDER, ("scalar_model_config.json", "scalar_model.safetensors"))
    return model, codec


def load_model(config_path, weights_path, *, device="cpu", backend="auto"):
    config = json.loads(Path(config_path).read_text())
    if config.get("architecture") != "spansynth_v8" or config.get("format_version") != 1:
        raise ValueError("Expected a public SpanSynth V8 inference checkpoint")
    if config.get("sample_rate") != 48000 or config.get("frames") != 512 or config.get("hop_length") != 1920:
        raise ValueError("Checkpoint audio geometry does not match the released model")
    with torch.device("meta"):
        model = SpanSynth(DiTConfig(**config["dit"]), EventSetConfig(**config["event_set"]), backend=backend)
    state = load_file(str(weights_path), device="cpu")
    model.load_state_dict(state, strict=True, assign=True)
    # Integer RoPE indices are reconstructed because they are not saved parameters.
    for block in model.blocks:
        rope = block.self_attention.rope
        rope.frequency_indices = torch.arange(0, rope.dim, 2, dtype=torch.int64)
    model = model.to(device=device).eval().requires_grad_(False)
    return model, config
