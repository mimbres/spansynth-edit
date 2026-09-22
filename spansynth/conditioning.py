# SPDX-FileCopyrightText: 2026 SpanSynth contributors
# SPDX-License-Identifier: Apache-2.0

"""Temporal and flow-time embeddings used by the released model."""
from __future__ import annotations
import math
from typing import Any
import torch
from torch import Tensor, nn

class TimestepEmbedding(nn.Module):
    """Embed scalar flow times with FP32 sinusoidal features and an MLP."""

    def __init__(
        self,
        hidden_dim: int,
        frequency_dim: int = 128,
        *,
        max_period: float = 10_000.0,
        scale: float = 1_000.0,
        device: torch.device | str | None = None,
        dtype: torch.dtype | None = None,
    ) -> None:
        super().__init__()
        if hidden_dim <= 0:
            raise ValueError(f"hidden_dim must be positive, got {hidden_dim}")
        if frequency_dim < 4 or frequency_dim % 2 != 0:
            raise ValueError(
                "frequency_dim must be an even integer of at least 4, "
                f"got {frequency_dim}"
            )
        if max_period <= 1.0:
            raise ValueError(f"max_period must be greater than 1, got {max_period}")
        if scale <= 0.0:
            raise ValueError(f"scale must be positive, got {scale}")

        self.hidden_dim = hidden_dim
        self.frequency_dim = frequency_dim
        self.max_period = float(max_period)
        self.scale = float(scale)

        factory_kwargs: dict[str, Any] = {"device": device, "dtype": dtype}
        self.mlp = nn.Sequential(
            nn.Linear(frequency_dim, hidden_dim, **factory_kwargs),
            nn.SiLU(),
            nn.Linear(hidden_dim, hidden_dim, **factory_kwargs),
        )

    def _sinusoidal_features(self, timestep: Tensor) -> Tensor:
        """Construct sinusoidal features in FP32 regardless of autocast state."""

        half_dim = self.frequency_dim // 2
        timestep_fp32 = timestep.reshape(-1).to(dtype=torch.float32)

        # These pointwise operations remain explicitly FP32 under AMP. The
        # denominator matches the temporal DiT implementation cited by SpanSynth.
        exponent = -math.log(self.max_period) * torch.arange(
            half_dim,
            device=timestep.device,
            dtype=torch.float32,
        ) / (half_dim - 1)
        frequencies = torch.exp(exponent)
        phase = self.scale * timestep_fp32[:, None] * frequencies[None, :]
        return torch.cat((phase.sin(), phase.cos()), dim=-1)

    def forward(self, timestep: Tensor) -> Tensor:
        """Return embeddings with shape ``[batch, hidden_dim]``.

        A scalar tensor is treated as a batch containing one timestep.
        """

        if timestep.ndim > 1:
            raise ValueError(
                "timestep must be a scalar or a one-dimensional tensor, "
                f"got shape {tuple(timestep.shape)}"
            )
        if not torch.is_floating_point(timestep):
            raise TypeError(
                f"timestep must be floating point, got {timestep.dtype}"
            )

        features = self._sinusoidal_features(timestep)
        features = features.to(dtype=self.mlp[0].weight.dtype)
        return self.mlp(features)


class TemporalConvPositionEmbedding(nn.Module):
    """Two-layer grouped convolutional position embedding for time sequences.

    This module returns only the positional embedding. The caller is expected
    to add it to the input residual. ``valid_mask`` follows the project-wide
    convention that ``True`` denotes a real token.
    """

    def __init__(
        self,
        hidden_dim: int,
        kernel_size: int = 31,
        groups: int = 16,
        *,
        device: torch.device | str | None = None,
        dtype: torch.dtype | None = None,
    ) -> None:
        super().__init__()
        if hidden_dim <= 0:
            raise ValueError(f"hidden_dim must be positive, got {hidden_dim}")
        if kernel_size <= 0 or kernel_size % 2 == 0:
            raise ValueError(
                f"kernel_size must be a positive odd integer, got {kernel_size}"
            )
        if groups <= 0:
            raise ValueError(f"groups must be positive, got {groups}")
        if hidden_dim % groups != 0:
            raise ValueError(
                f"hidden_dim ({hidden_dim}) must be divisible by groups ({groups})"
            )

        self.hidden_dim = hidden_dim
        self.kernel_size = kernel_size
        self.groups = groups

        factory_kwargs: dict[str, Any] = {"device": device, "dtype": dtype}
        conv_kwargs: dict[str, Any] = {
            "kernel_size": kernel_size,
            "padding": kernel_size // 2,
            "groups": groups,
            **factory_kwargs,
        }
        self.conv1 = nn.Conv1d(hidden_dim, hidden_dim, **conv_kwargs)
        self.activation1 = nn.Mish()
        self.conv2 = nn.Conv1d(hidden_dim, hidden_dim, **conv_kwargs)
        self.activation2 = nn.Mish()

    def _validate_mask(self, x: Tensor, valid_mask: Tensor) -> None:
        if valid_mask.dtype != torch.bool:
            raise TypeError(
                f"valid_mask must have dtype bool, got {valid_mask.dtype}"
            )
        if valid_mask.ndim != 2 or valid_mask.shape != x.shape[:2]:
            raise ValueError(
                "valid_mask must have shape [batch, time] matching x, "
                f"got {tuple(valid_mask.shape)} for x {tuple(x.shape)}"
            )
        if valid_mask.device != x.device:
            raise ValueError(
                f"valid_mask and x must share a device, got "
                f"{valid_mask.device} and {x.device}"
            )
        if not bool(valid_mask.any(dim=1).all()):
            raise ValueError("every sample must contain at least one valid token")

    @staticmethod
    def _zero_invalid(x: Tensor, channel_mask: Tensor | None) -> Tensor:
        if channel_mask is None:
            return x
        return x.masked_fill(~channel_mask, 0.0)

    def forward(self, x: Tensor, valid_mask: Tensor | None = None) -> Tensor:
        """Return temporal position features with the same shape as ``x``."""

        if x.ndim != 3:
            raise ValueError(
                f"x must have shape [batch, time, hidden], got {tuple(x.shape)}"
            )
        if x.shape[-1] != self.hidden_dim:
            raise ValueError(
                f"expected hidden dimension {self.hidden_dim}, got {x.shape[-1]}"
            )
        if not torch.is_floating_point(x):
            raise TypeError(f"x must be floating point, got {x.dtype}")

        channel_mask: Tensor | None = None
        if valid_mask is not None:
            self._validate_mask(x, valid_mask)
            channel_mask = valid_mask.unsqueeze(1)

        hidden = x.transpose(1, 2)
        hidden = self._zero_invalid(hidden, channel_mask)
        hidden = self.conv1(hidden)
        hidden = self._zero_invalid(hidden, channel_mask)
        hidden = self.activation1(hidden)
        hidden = self.conv2(hidden)
        hidden = self._zero_invalid(hidden, channel_mask)
        hidden = self.activation2(hidden)
        hidden = self._zero_invalid(hidden, channel_mask)
        return hidden.transpose(1, 2)
