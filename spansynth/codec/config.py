# SPDX-FileCopyrightText: 2026 SpanSynth contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class ScalarModelConfig:
    sample_rate: int = 48000
    num_bands: int = 1
    init_channel: int = 64
    channel_mults: tuple[int, ...] = (2, 2, 2, 2, 2)
    downsample_factors: tuple[int, ...] = (3, 4, 4, 4, 5)
    downsample_kernel_sizes: tuple[int, ...] = (6, 8, 8, 8, 10)
    upsample_factors: tuple[int, ...] = (5, 4, 4, 4, 3)
    upsample_kernel_sizes: tuple[int, ...] = (10, 8, 8, 8, 6)
    latent_hidden_dim: int = 128
    default_kernel_size: int = 7
    delay_kernel_size: int = 5
    num_samples: int = 2
    causal: bool = True
    res_kernel_size: int = 7
    mode: str = "pre_proj"
    config_source_notes: dict[str, str] | None = None

    @classmethod
    def from_obj(cls, value: "ScalarModelConfig | Mapping[str, Any]") -> "ScalarModelConfig":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError(f"Expected ScalarModelConfig or mapping, got {type(value)!r}")

        names = {field.name for field in fields(cls)}
        data = {key: value[key] for key in names if key in value}
        for key in (
            "channel_mults",
            "downsample_factors",
            "downsample_kernel_sizes",
            "upsample_factors",
            "upsample_kernel_sizes",
        ):
            if key in data:
                data[key] = tuple(int(v) for v in data[key])
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in (
            "channel_mults",
            "downsample_factors",
            "downsample_kernel_sizes",
            "upsample_factors",
            "upsample_kernel_sizes",
        ):
            data[key] = list(data[key])
        return data


SQ_CONFIG_KEYS = (
    "sample_rate",
    "num_bands",
    "init_channel",
    "channel_mults",
    "downsample_factors",
    "downsample_kernel_sizes",
    "upsample_factors",
    "upsample_kernel_sizes",
    "latent_hidden_dim",
    "default_kernel_size",
    "delay_kernel_size",
    "num_samples",
    "causal",
    "res_kernel_size",
    "mode",
)


def load_config(path: str | Path) -> ScalarModelConfig:
    with Path(path).open("r", encoding="utf-8") as f:
        return ScalarModelConfig.from_obj(json.load(f))


def save_config(config: ScalarModelConfig, path: str | Path) -> None:
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=2, sort_keys=True)
        f.write("\n")


