# SPDX-FileCopyrightText: 2026 SpanSynth contributors
# SPDX-License-Identifier: Apache-2.0

"""Bidirectional self-attention with the checkpoint's interleaved RoPE."""
from __future__ import annotations
import math
from contextlib import nullcontext
import torch
from torch import Tensor, nn
from torch.nn import functional as F

class RotaryEmbedding1D(nn.Module):
    """Apply interleaved one-dimensional RoPE with FP32 trigonometry.

    Inputs use ``[batch, heads, time, head_dim]`` layout. The rotation is
    evaluated in FP32 even under autocast, then cast back to the input dtype.
    """

    def __init__(self, dim: int, theta: float = 10_000.0) -> None:
        super().__init__()
        if dim <= 0 or dim % 2 != 0:
            raise ValueError(f"RoPE dimension must be a positive even integer, got {dim}.")
        if theta <= 0:
            raise ValueError(f"RoPE theta must be positive, got {theta}.")

        self.dim = dim
        self.theta = float(theta)
        # Keep only integer indices as module state. Floating buffers are cast
        # by ``module.to(dtype=...)``; storing precomputed FP32 frequencies
        # would therefore permanently quantize RoPE when a module is moved to
        # BF16. The small FP32 frequency vector is reconstructed per forward.
        self.register_buffer(
            "frequency_indices",
            torch.arange(0, dim, 2, dtype=torch.int64),
            persistent=False,
        )

    def forward(self, x: Tensor, positions: Tensor | None = None) -> Tensor:
        """Rotate ``x`` at the supplied absolute temporal positions."""

        if x.ndim != 4:
            raise ValueError(
                "RoPE input must have shape [batch, heads, time, head_dim], "
                f"got {tuple(x.shape)}."
            )
        if x.shape[-1] != self.dim:
            raise ValueError(
                f"RoPE expected head_dim={self.dim}, got {x.shape[-1]}."
            )
        if not torch.is_floating_point(x):
            raise TypeError(f"RoPE input must be floating point, got {x.dtype}.")

        batch_size, _, sequence_length, _ = x.shape
        if positions is None:
            positions_fp32 = torch.arange(
                sequence_length, device=x.device, dtype=torch.float32
            )
        else:
            if positions.ndim == 1:
                if positions.shape[0] != sequence_length:
                    raise ValueError(
                        "One-dimensional RoPE positions must have shape [time], "
                        f"got {tuple(positions.shape)} for time={sequence_length}."
                    )
            elif positions.ndim == 2:
                if positions.shape != (batch_size, sequence_length):
                    raise ValueError(
                        "Two-dimensional RoPE positions must have shape [batch, time], "
                        f"got {tuple(positions.shape)} for batch={batch_size}, "
                        f"time={sequence_length}."
                    )
            else:
                raise ValueError(
                    "RoPE positions must have shape [time] or [batch, time], "
                    f"got {tuple(positions.shape)}."
                )
            positions_fp32 = positions.to(device=x.device, dtype=torch.float32)

        exponent = self.frequency_indices.to(device=x.device, dtype=torch.float32) / self.dim
        inv_freq = torch.exp(-math.log(self.theta) * exponent)
        angles = positions_fp32.unsqueeze(-1) * inv_freq
        if positions_fp32.ndim == 1:
            cos = angles.cos().unsqueeze(0).unsqueeze(0)
            sin = angles.sin().unsqueeze(0).unsqueeze(0)
        else:
            cos = angles.cos().unsqueeze(1)
            sin = angles.sin().unsqueeze(1)

        x_fp32 = x.float()
        x_even = x_fp32[..., 0::2]
        x_odd = x_fp32[..., 1::2]
        rotated_even = x_even * cos - x_odd * sin
        rotated_odd = x_even * sin + x_odd * cos
        rotated = torch.stack((rotated_even, rotated_odd), dim=-1).flatten(-2)
        return rotated.to(dtype=x.dtype)


class MultiHeadAttention(nn.Module):
    def __init__(self, dim, num_heads, *, backend="auto", rope_theta=10_000.0):
        super().__init__()
        self.num_heads = num_heads
        self.head_dim = dim // num_heads
        self.backend = backend
        self.q_proj = nn.Linear(dim, dim)
        self.k_proj = nn.Linear(dim, dim)
        self.v_proj = nn.Linear(dim, dim)
        self.out_proj = nn.Linear(dim, dim)
        self.rope = RotaryEmbedding1D(self.head_dim, rope_theta)

    def forward(self, x):
        batch, frames, dim = x.shape
        def split(value):
            return value.reshape(batch, frames, self.num_heads, self.head_dim).transpose(1, 2)
        query = self.rope(split(self.q_proj(x)))
        key = self.rope(split(self.k_proj(x)))
        value = split(self.v_proj(x))
        context = nullcontext()
        if self.backend != "auto":
            from torch.nn.attention import SDPBackend, sdpa_kernel
            backend = {"math": SDPBackend.MATH, "flash": SDPBackend.FLASH_ATTENTION}[self.backend]
            context = sdpa_kernel(backend)
        with context:
            attended = F.scaled_dot_product_attention(query, key, value, dropout_p=0.0)
        return self.out_proj(attended.transpose(1, 2).reshape(batch, frames, dim))
