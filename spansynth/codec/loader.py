"""Strict loading of the frozen Base SQ codec."""
from pathlib import Path
import torch
from safetensors.torch import load_file
from .config import load_config
from .sq_codec import ScalarModel


def load_scalar_model(weights_path: str | Path, config_path: str | Path,
                      device="cpu", dtype=torch.float32):
    config = load_config(config_path)
    if config.sample_rate != 48000 or config.latent_hidden_dim != 128 or not config.causal:
        raise ValueError("Expected the 48 kHz causal Base SQ codec with 128 channels")
    with torch.device("meta"):
        model = ScalarModel(config)
    state = load_file(str(weights_path), device="cpu")
    model.load_state_dict(state, strict=True, assign=True)
    return model.to(device=device, dtype=dtype).eval().requires_grad_(False)
