# SPDX-FileCopyrightText: 2026 SpanSynth contributors
# SPDX-License-Identifier: Apache-2.0

"""Inference-only V8 velocity model with the released checkpoint parameter names."""
from __future__ import annotations
import math
from dataclasses import dataclass
import torch
from torch import Tensor, nn
from .attention import MultiHeadAttention
from .conditioning import TemporalConvPositionEmbedding, TimestepEmbedding


@dataclass(frozen=True)
class DiTConfig:
    latent_dim: int = 128
    midi_dim: int = 768
    hidden_size: int = 1024
    depth: int = 25
    num_heads: int = 16
    mlp_ratio: float = 4.0
    time_frequency_dim: int = 128
    conv_pos_kernel_size: int = 31
    conv_pos_groups: int = 16
    rope_theta: float = 10_000.0
    norm_eps: float = 1e-6

    def __post_init__(self):
        if self.latent_dim != 128 or self.midi_dim != 768:
            raise ValueError("V8 requires 128 SQ channels and 768 MIDI channels")
        if self.depth < 1 or self.num_heads < 1 or self.hidden_size % self.num_heads:
            raise ValueError("Invalid DiT depth or attention geometry")


def _modulate(x, norm, shift, scale):
    return (norm(x.float()) * (1 + scale.float()[:, None]) + shift.float()[:, None]).to(x.dtype)


class FeedForward(nn.Module):
    def __init__(self, dim, ratio):
        super().__init__()
        # Keep index 3 for the output projection to load the published weights.
        self.net = nn.Sequential(nn.Linear(dim, int(dim * ratio)), nn.GELU(approximate="tanh"),
                                 nn.Identity(), nn.Linear(int(dim * ratio), dim))

    def forward(self, x):
        return self.net(x)


class DiTBlock(nn.Module):
    def __init__(self, config, backend):
        super().__init__()
        dim = config.hidden_size
        self.self_norm = nn.LayerNorm(dim, elementwise_affine=False, eps=config.norm_eps)
        self.ff_norm = nn.LayerNorm(dim, elementwise_affine=False, eps=config.norm_eps)
        self.modulation = nn.Sequential(nn.SiLU(), nn.Linear(dim, 6 * dim))
        self.self_attention = MultiHeadAttention(dim, config.num_heads, backend=backend,
                                                 rope_theta=config.rope_theta)
        self.feed_forward = FeedForward(dim, config.mlp_ratio)

    def forward(self, x, time):
        with torch.autocast(device_type=x.device.type, enabled=False):
            shift_a, scale_a, gate_a, shift_f, scale_f, gate_f = self.modulation(time.float()).chunk(6, -1)
        attended = self.self_attention(_modulate(x, self.self_norm, shift_a, scale_a))
        x = x + gate_a[:, None].to(attended.dtype) * attended
        fed = self.feed_forward(_modulate(x, self.ff_norm, shift_f, scale_f))
        return x + gate_f[:, None].to(fed.dtype) * fed


class FinalLayer(nn.Module):
    def __init__(self, config):
        super().__init__()
        dim = config.hidden_size
        self.norm = nn.LayerNorm(dim, elementwise_affine=False, eps=config.norm_eps)
        self.modulation = nn.Sequential(nn.SiLU(), nn.Linear(dim, 2 * dim))
        self.projection = nn.Linear(dim, config.latent_dim)

    def forward(self, x, time):
        with torch.autocast(device_type=x.device.type, enabled=False):
            shift, scale = self.modulation(time.float()).chunk(2, -1)
        return self.projection(_modulate(x, self.norm, shift, scale))

@dataclass(frozen=True, slots=True)
class EventSetConfig:
    """Fixed K128 event geometry with six pooled 128D summaries."""

    capacity: int = 128
    kind_embedding_dim: int = 16
    pitch_embedding_dim: int = 32
    numeric_dim: int = 7
    row_hidden_dim: int = 256
    row_dim: int = 128
    learned_pool_tokens: int = 5
    output_dim: int = 768
    row_chunk_size: int = 16
    norm_eps: float = 1e-6

    def __post_init__(self) -> None:
        locked = {
            "capacity": (self.capacity, 128),
            "kind_embedding_dim": (self.kind_embedding_dim, 16),
            "pitch_embedding_dim": (self.pitch_embedding_dim, 32),
            "numeric_dim": (self.numeric_dim, 7),
            "row_hidden_dim": (self.row_hidden_dim, 256),
            "row_dim": (self.row_dim, 128),
            "learned_pool_tokens": (
                self.learned_pool_tokens,
                5,
            ),
            "output_dim": (self.output_dim, 768),
        }
        for name, (value, expected) in locked.items():
            if value != expected:
                raise ValueError(f"{name} must be {expected}")
        if self.output_dim != (self.learned_pool_tokens + 1) * self.row_dim:
            raise ValueError("event output must concatenate all-sum and pool tokens")
        if (
            isinstance(self.row_chunk_size, bool)
            or not isinstance(self.row_chunk_size, int)
            or not 1 <= self.row_chunk_size <= self.capacity
        ):
            raise ValueError("row_chunk_size must be an integer in [1, capacity]")
        if not isinstance(self.norm_eps, (int, float)) or self.norm_eps <= 0.0:
            raise ValueError("norm_eps must be positive")

    @property
    def row_input_dim(self) -> int:
        """Return categorical embeddings plus the numeric-row width."""

        return self.kind_embedding_dim + self.pitch_embedding_dim + self.numeric_dim

class EventSetEncoder(nn.Module):
    def __init__(self, config: EventSetConfig | None = None) -> None:
        super().__init__()
        self.config = config or EventSetConfig()
        config = self.config

        self.kind_embedding = nn.Embedding(
            4,
            config.kind_embedding_dim,
            padding_idx=0,
        )
        self.pitch_embedding = nn.Embedding(
            129,
            config.pitch_embedding_dim,
            padding_idx=0,
        )
        self.row_mlp = nn.Sequential(
            nn.Linear(config.row_input_dim, config.row_hidden_dim),
            nn.SiLU(),
            nn.Linear(config.row_hidden_dim, config.row_dim),
            nn.RMSNorm(config.row_dim, eps=config.norm_eps),
        )
        self.pool_queries = nn.Parameter(
            torch.empty(config.learned_pool_tokens, config.row_dim)
        )
        nn.init.normal_(self.pool_queries, mean=0.0, std=0.02)
        self.pool_norm = nn.RMSNorm(config.row_dim, eps=config.norm_eps)
        self.count_projection = nn.Linear(
            5,
            config.output_dim,
            bias=False,
        )
        self.row_chunk_size = config.row_chunk_size

    def _validate_inputs(
        self,
        kind_id: Tensor,
        pitch_id: Tensor,
        numeric: Tensor,
        event_valid: Tensor,
        category_id: Tensor | None,
    ) -> None:
        expected_rows = kind_id.shape
        if kind_id.ndim != 3:
            raise ValueError("kind_id must have shape [B, T, K]")
        if expected_rows[-1] != self.config.capacity:
            raise ValueError(f"kind_id must use K={self.config.capacity}")
        if pitch_id.shape != expected_rows:
            raise ValueError("pitch_id must match kind_id shape")
        if numeric.shape != (*expected_rows, self.config.numeric_dim):
            raise ValueError("numeric must have shape [B, T, K, 7]")
        if event_valid.shape != expected_rows:
            raise ValueError("event_valid must match kind_id shape")
        if category_id is not None and category_id.shape != expected_rows:
            raise ValueError("category_id must match kind_id shape")
        if kind_id.dtype not in (torch.int32, torch.int64):
            raise TypeError("kind_id must use an integer dtype")
        if pitch_id.dtype not in (torch.int32, torch.int64):
            raise TypeError("pitch_id must use an integer dtype")
        if not torch.is_floating_point(numeric):
            raise TypeError("numeric must be floating point")
        if event_valid.dtype is not torch.bool:
            raise TypeError("event_valid must use torch.bool")
        if category_id is not None and category_id.dtype not in (
            torch.int32,
            torch.int64,
        ):
            raise TypeError("category_id must use an integer dtype")
        if not (
            kind_id.device
            == pitch_id.device
            == numeric.device
            == event_valid.device
        ):
            raise ValueError("all event-set tensors must share one device")
        if category_id is not None and category_id.device != kind_id.device:
            raise ValueError("all event-set tensors must share one device")

    def _counts(
        self,
        kind_id: Tensor,
        numeric: Tensor,
        event_valid: Tensor,
        *,
        dtype: torch.dtype,
    ) -> Tensor:
        drum = event_valid & (numeric[..., 0] > 0.0)
        melodic = event_valid & ~drum
        count_values = torch.stack(
            (
                melodic.sum(dim=2),
                drum.sum(dim=2),
                (event_valid & (kind_id == 1)).sum(dim=2),
                (event_valid & (kind_id == 2)).sum(dim=2),
                (event_valid & (kind_id == 3)).sum(dim=2),
            ),
            dim=-1,
        ).to(dtype=dtype)
        return torch.log1p(count_values) / math.log1p(self.config.capacity)

    def _encode_and_pool_packed_chunk(
        self,
        kind_id: Tensor,
        pitch_id: Tensor,
        numeric: Tensor,
        event_valid: Tensor,
        category_id: Tensor | None = None,
    ) -> tuple[Tensor, Tensor]:
        """Encode only valid rows and reduce them directly into frame pools."""

        del category_id
        batch, frames, _ = kind_id.shape
        positions = event_valid.nonzero(as_tuple=False)
        frame_index = positions[:, 0] * frames + positions[:, 1]
        packed_kind = kind_id[event_valid].to(dtype=torch.int64)
        packed_pitch = pitch_id[event_valid].to(dtype=torch.int64)
        kind = self.kind_embedding(packed_kind)
        pitch = self.pitch_embedding(packed_pitch)
        values = numeric[event_valid].to(dtype=kind.dtype)
        encoded = self.row_mlp(torch.cat((kind, pitch, values), dim=-1))

        all_sum = encoded.new_zeros(
            batch * frames,
            self.config.row_dim,
        ).index_add(0, frame_index, encoded)
        logits = encoded @ self.pool_queries.transpose(0, 1)
        gates = (logits * (1.0 / math.sqrt(self.config.row_dim))).sigmoid()
        contributions = gates[..., None] * encoded[:, None, :]
        gated_sums = encoded.new_zeros(
            batch * frames,
            self.config.learned_pool_tokens,
            self.config.row_dim,
        ).index_add(0, frame_index, contributions)
        return (
            all_sum.reshape(batch, frames, self.config.row_dim),
            gated_sums.reshape(
                batch,
                frames,
                self.config.learned_pool_tokens,
                self.config.row_dim,
            ),
        )

    def forward(
        self,
        kind_id: Tensor,
        pitch_id: Tensor,
        numeric: Tensor,
        event_valid: Tensor,
        *,
        category_id: Tensor | None = None,
    ) -> Tensor:
        """Return the frame condition without evaluating padded MIDI rows."""

        self._validate_inputs(
            kind_id,
            pitch_id,
            numeric,
            event_valid,
            category_id=category_id,
        )
        batch, frames, capacity = kind_id.shape
        all_sum: Tensor | None = None
        gated_sums: Tensor | None = None
        for start in range(0, capacity, self.config.row_chunk_size):
            stop = min(start + self.config.row_chunk_size, capacity)
            chunk_args = (
                kind_id[:, :, start:stop],
                pitch_id[:, :, start:stop],
                numeric[:, :, start:stop],
                event_valid[:, :, start:stop],
            )
            if category_id is not None:
                chunk_args = (*chunk_args, category_id[:, :, start:stop])
            chunk_all, chunk_gated = self._encode_and_pool_packed_chunk(
                *chunk_args
            )
            all_sum = chunk_all if all_sum is None else all_sum + chunk_all
            gated_sums = (
                chunk_gated if gated_sums is None else gated_sums + chunk_gated
            )

        if all_sum is None or gated_sums is None:
            raise RuntimeError("event-set capacity produced no row chunks")
        pooled = torch.cat((all_sum[:, :, None, :], gated_sums), dim=2)
        nonempty = event_valid.any(dim=2)
        pooled = self.pool_norm(pooled)
        pooled = pooled.masked_fill(~nonempty[:, :, None, None], 0.0)
        condition = pooled.reshape(batch, frames, self.config.output_dim)
        count_features = self._counts(
            kind_id,
            numeric,
            event_valid,
            dtype=condition.dtype,
        )
        condition = condition + self.count_projection(count_features)
        return condition.masked_fill(~nonempty[..., None], 0.0)


class SpanSynth(nn.Module):
    """V8 model. MIDI and clean audio projections are cached by the sampler."""

    def __init__(self, dit: DiTConfig, event_set: EventSetConfig, backend="auto"):
        super().__init__()
        self.midi_encoder = EventSetEncoder(event_set)
        self.input_projection = nn.Linear(dit.latent_dim + dit.midi_dim + 2, dit.hidden_size)
        self.clean_reference_projection = nn.Linear(dit.latent_dim, dit.hidden_size, bias=False)
        self.position_embedding = TemporalConvPositionEmbedding(
            dit.hidden_size, kernel_size=dit.conv_pos_kernel_size, groups=dit.conv_pos_groups)
        self.timestep_embedding = TimestepEmbedding(dit.hidden_size, dit.time_frequency_dim)
        self.blocks = nn.ModuleList([DiTBlock(dit, backend) for _ in range(dit.depth)])
        self.final_layer = FinalLayer(dit)

    def encode_midi(self, rows):
        return self.midi_encoder(rows["kind_id"], rows["pitch_id"], rows["numeric"],
                                 rows["event_valid"], category_id=rows["category_id"])

    def prepare_clean(self, clean, observed):
        return self.clean_reference_projection(
            clean.masked_fill(~observed[..., None], 0).to(self.clean_reference_projection.weight.dtype)
        ).masked_fill(~observed[..., None], 0)

    def forward(self, state, time, observed, midi, reference_available, clean_hidden):
        dtype = self.input_projection.weight.dtype
        joined = torch.cat((state.to(dtype), midi.to(dtype), observed[..., None].to(dtype),
                            reference_available[..., None].to(dtype)), dim=-1)
        hidden = self.input_projection(joined)
        hidden = hidden + clean_hidden.to(hidden.dtype)
        # All 512 frames are valid, including the explicit zero-padded audio tail.
        hidden = hidden + self.position_embedding(hidden)
        time_embedding = self.timestep_embedding(time)
        for block in self.blocks:
            hidden = block(hidden, time_embedding)
        return self.final_layer(hidden, time_embedding)
