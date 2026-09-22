# SPDX-FileCopyrightText: 2026 SpanSynth contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Mapping, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils.parametrizations import weight_norm

from .config import ScalarModelConfig


BASE_RESIDUAL_UNITS_PER_DECODER_STAGE = 5


def get_padding(kernel_size: int, dilation: int = 1) -> int:
    return int((kernel_size * dilation - dilation) / 2)


@torch.jit.script
def snake(x, alpha):
    shape = x.shape
    x = x.reshape(shape[0], shape[1], -1)
    x = x + (alpha + 1e-9).reciprocal() * torch.sin(alpha * x).pow(2)
    x = x.reshape(shape)
    return x


class Snake1d(nn.Module):
    def __init__(self, channels: int):
        super().__init__()
        self.alpha = nn.Parameter(torch.ones(1, channels, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return snake(x, self.alpha)


class Conv1d(nn.Conv1d):
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int,
        stride: int = 1,
        dilation: int = 1,
        groups: int = 1,
        padding_mode: str = "zeros",
        bias: bool = True,
        padding: int | None = None,
        causal: bool = False,
        w_init_gain: str | None = None,
    ):
        self.causal = causal
        self.left_padding = 0
        if padding is None:
            if causal:
                padding = 0
                self.left_padding = dilation * (kernel_size - 1)
            else:
                padding = get_padding(kernel_size, dilation)
        super().__init__(
            in_channels,
            out_channels,
            kernel_size,
            stride=stride,
            padding=padding,
            dilation=dilation,
            groups=groups,
            padding_mode=padding_mode,
            bias=bias,
        )
        if w_init_gain is not None:
            torch.nn.init.xavier_uniform_(
                self.weight, gain=torch.nn.init.calculate_gain(w_init_gain)
            )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.causal:
            x = F.pad(x.unsqueeze(2), (self.left_padding, 0, 0, 0)).squeeze(2)
        return super().forward(x)


class ConvTranspose1d(nn.ConvTranspose1d):
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int,
        stride: int = 1,
        output_padding: int = 0,
        groups: int = 1,
        bias: bool = True,
        dilation: int = 1,
        padding: int | None = None,
        padding_mode: str = "zeros",
        causal: bool = False,
    ):
        if padding is None:
            padding = 0 if causal else (kernel_size - stride) // 2
        if causal:
            assert padding == 0, "padding is not allowed in causal ConvTranspose1d."
            assert kernel_size == 2 * stride, (
                "kernel_size must be equal to 2*stride in causal ConvTranspose1d."
            )
        super().__init__(
            in_channels,
            out_channels,
            kernel_size,
            stride=stride,
            padding=padding,
            output_padding=output_padding,
            groups=groups,
            bias=bias,
            dilation=dilation,
            padding_mode=padding_mode,
        )
        self.causal = causal
        self.stride = stride

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = super().forward(x)
        if self.causal:
            x = x[:, :, : -self.stride]
        return x


class PreProcessor(nn.Module):
    def __init__(self, n_in: int, n_out: int, num_samples: int, kernel_size=7, causal=False):
        super().__init__()
        self.pooling = torch.nn.AvgPool1d(kernel_size=num_samples)
        self.conv = Conv1d(n_in, n_out, kernel_size=kernel_size, causal=causal)
        self.activation = nn.PReLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        output = self.activation(self.conv(x))
        output = self.pooling(output)
        return output


class PostProcessor(nn.Module):
    def __init__(self, n_in: int, n_out: int, num_samples: int, kernel_size=7, causal=False):
        super().__init__()
        self.num_samples = num_samples
        self.conv = Conv1d(n_in, n_out, kernel_size=kernel_size, causal=causal)
        self.activation = nn.PReLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = torch.transpose(x, 1, 2)
        bsz, time, channels = x.size()
        x = x.repeat(1, 1, self.num_samples).view(bsz, -1, channels)
        x = torch.transpose(x, 1, 2)
        output = self.activation(self.conv(x))
        return output


class ResidualUnit(nn.Module):
    def __init__(self, n_in: int, n_out: int, dilation: int, res_kernel_size=7, causal=False):
        super().__init__()
        self.conv1 = weight_norm(
            Conv1d(n_in, n_out, kernel_size=res_kernel_size, dilation=dilation, causal=causal)
        )
        self.conv2 = weight_norm(Conv1d(n_in, n_out, kernel_size=1, causal=causal))
        self.activation1 = nn.PReLU()
        self.activation2 = nn.PReLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        output = self.activation1(self.conv1(x))
        output = self.activation2(self.conv2(output))
        return output + x


class ResEncoderBlock(nn.Module):
    def __init__(
        self, n_in: int, n_out: int, stride: int, down_kernel_size: int, res_kernel_size=7, causal=False
    ):
        super().__init__()
        self.convs = nn.ModuleList(
            [
                ResidualUnit(n_in, n_out // 2, dilation=1, res_kernel_size=res_kernel_size, causal=causal),
                ResidualUnit(n_out // 2, n_out // 2, dilation=3, res_kernel_size=res_kernel_size, causal=causal),
                ResidualUnit(n_out // 2, n_out // 2, dilation=5, res_kernel_size=res_kernel_size, causal=causal),
                ResidualUnit(n_out // 2, n_out // 2, dilation=7, res_kernel_size=res_kernel_size, causal=causal),
                ResidualUnit(n_out // 2, n_out // 2, dilation=9, res_kernel_size=res_kernel_size, causal=causal),
            ]
        )
        self.down_conv = DownsampleLayer(n_in, n_out, down_kernel_size, stride=stride, causal=causal)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for conv in self.convs:
            x = conv(x)
        x = self.down_conv(x)
        return x


class ResDecoderBlock(nn.Module):
    def __init__(
        self, n_in: int, n_out: int, stride: int, up_kernel_size: int, res_kernel_size=7, causal=False
    ):
        super().__init__()
        self.up_conv = UpsampleLayer(
            n_in, n_out, kernel_size=up_kernel_size, stride=stride, causal=causal, activation=None
        )
        self.convs = nn.ModuleList(
            [
                ResidualUnit(n_out, n_out, dilation=1, res_kernel_size=res_kernel_size, causal=causal),
                ResidualUnit(n_out, n_out, dilation=3, res_kernel_size=res_kernel_size, causal=causal),
                ResidualUnit(n_out, n_out, dilation=5, res_kernel_size=res_kernel_size, causal=causal),
                ResidualUnit(n_out, n_out, dilation=7, res_kernel_size=res_kernel_size, causal=causal),
                ResidualUnit(n_out, n_out, dilation=9, res_kernel_size=res_kernel_size, causal=causal),
            ]
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.up_conv(x)
        for conv in self.convs:
            x = conv(x)
        return x




class DownsampleLayer(nn.Module):
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int,
        stride: int = 1,
        causal: bool = False,
        activation=nn.PReLU(),
        use_weight_norm: bool = True,
        pooling: bool = False,
    ):
        super().__init__()
        self.pooling = pooling
        self.stride = stride
        self.activation = nn.PReLU()
        self.use_weight_norm = use_weight_norm
        if pooling:
            self.layer = Conv1d(in_channels, out_channels, kernel_size, causal=causal)
            self.pooling = nn.AvgPool1d(kernel_size=stride)
        else:
            self.layer = Conv1d(in_channels, out_channels, kernel_size, stride=stride, causal=causal)
        if use_weight_norm:
            self.layer = weight_norm(self.layer)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.layer(x)
        x = self.activation(x) if self.activation is not None else x
        if self.pooling:
            x = self.pooling(x)
        return x


class UpsampleLayer(nn.Module):
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int,
        stride: int = 1,
        causal: bool = False,
        activation=nn.PReLU(),
        use_weight_norm: bool = True,
        repeat: bool = False,
    ):
        super().__init__()
        self.repeat = repeat
        self.stride = stride
        self.activation = activation
        self.use_weight_norm = use_weight_norm
        if repeat:
            self.layer = Conv1d(in_channels, out_channels, kernel_size, causal=causal)
        else:
            self.layer = ConvTranspose1d(in_channels, out_channels, kernel_size, stride=stride, causal=causal)
        if use_weight_norm:
            self.layer = weight_norm(self.layer)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.layer(x)
        x = self.activation(x) if self.activation is not None else x
        if self.repeat:
            x = torch.transpose(x, 1, 2)
            bsz, time, channels = x.size()
            x = x.repeat(1, 1, self.stride).view(bsz, -1, channels)
            x = torch.transpose(x, 1, 2)
        return x


class round_func9:
    @staticmethod
    def apply(value):
        return torch.round(value * 9.0) / 9.0


class ScalarModel(nn.Module):
    def __init__(self, config: ScalarModelConfig | Mapping | None = None, **kwargs):
        super().__init__()
        if config is None:
            config = {}
        if kwargs:
            config = {**dict(config), **kwargs} if isinstance(config, Mapping) else {**config.to_dict(), **kwargs}
        cfg = ScalarModelConfig.from_obj(config)
        self.config = cfg
        self.vq = round_func9()
        self.mode = cfg.mode
        self.encoder = []
        self.decoder = []

        self.encoder.append(
            weight_norm(
                Conv1d(cfg.num_bands, cfg.init_channel, kernel_size=cfg.default_kernel_size, causal=cfg.causal)
            )
        )
        if cfg.num_samples > 1:
            self.encoder.append(
                PreProcessor(
                    cfg.init_channel,
                    cfg.init_channel,
                    cfg.num_samples,
                    kernel_size=cfg.default_kernel_size,
                    causal=cfg.causal,
                )
            )
        for i, down_factor in enumerate(cfg.downsample_factors):
            self.encoder.append(
                ResEncoderBlock(
                    int(cfg.init_channel * (2**i)),
                    int(cfg.init_channel * (2 ** (i + 1))),
                    int(down_factor),
                    int(cfg.downsample_kernel_sizes[i]),
                    cfg.res_kernel_size,
                    causal=cfg.causal,
                )
            )
        self.encoder.append(
            weight_norm(
                Conv1d(
                    int(cfg.init_channel * (2 ** len(cfg.downsample_factors))),
                    cfg.latent_hidden_dim,
                    kernel_size=cfg.default_kernel_size,
                    causal=cfg.causal,
                )
            )
        )

        self.decoder.append(
            weight_norm(
                Conv1d(
                    cfg.latent_hidden_dim,
                    int(cfg.init_channel * (2 ** len(cfg.upsample_factors))),
                    kernel_size=cfg.delay_kernel_size,
                )
            )
        )
        for i, upsample_factor in enumerate(cfg.upsample_factors):
            self.decoder.append(
                ResDecoderBlock(
                    int(cfg.init_channel * (2 ** (len(cfg.upsample_factors) - i))),
                    int(cfg.init_channel * (2 ** (len(cfg.upsample_factors) - i - 1))),
                    int(upsample_factor),
                    int(cfg.upsample_kernel_sizes[i]),
                    cfg.res_kernel_size,
                    causal=cfg.causal,
                )
            )
        if cfg.num_samples > 1:
            self.decoder.append(
                PostProcessor(
                    cfg.init_channel,
                    cfg.init_channel,
                    cfg.num_samples,
                    kernel_size=cfg.default_kernel_size,
                    causal=cfg.causal,
                )
            )
        self.decoder.append(
            weight_norm(
                Conv1d(cfg.init_channel, cfg.num_bands, kernel_size=cfg.default_kernel_size, causal=cfg.causal)
            )
        )
        self.encoder = nn.ModuleList(self.encoder)
        self.decoder = nn.ModuleList(self.decoder)

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        for i, layer in enumerate(self.encoder):
            if i != len(self.encoder) - 1:
                x = layer(x)
            else:
                x = torch.tanh(layer(x))
        return x

    def quantize(self, z: torch.Tensor) -> torch.Tensor:
        return self.vq.apply(z)

    def decode(self, zq: torch.Tensor) -> torch.Tensor:
        x = self.vq.apply(zq)
        for layer in self.decoder:
            x = layer(x)
        return x

    def inference(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        emb = self.encode(x)
        emb_quant = self.quantize(emb)
        x_hat = self.decode(emb_quant)
        return emb, emb_quant, x_hat

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decode(self.quantize(self.encode(x)))
