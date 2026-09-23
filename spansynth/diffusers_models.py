"""Optional Diffusers components for the released SpanSynth and HeartCodec weights."""

from diffusers import ConfigMixin, ModelMixin
from diffusers.configuration_utils import register_to_config
from diffusers.utils.accelerate_utils import apply_forward_hook
import torch

from spansynth.codec.config import ScalarModelConfig
from spansynth.codec.sq_codec import ScalarModel
from spansynth.model import DiTConfig, EventSetConfig, SpanSynth


class SpanSynthTransformerModel(SpanSynth, ModelMixin, ConfigMixin):
    """The reference velocity model with Diffusers configuration and weight loading."""

    _no_split_modules = ["SpanSynthTransformerModel"]
    _keep_in_fp32_modules = ["modulation", "timestep_embedding"]

    @register_to_config
    def __init__(self, dit: dict, event_set: dict):
        super().__init__(DiTConfig(**dit), EventSetConfig(**event_set))
        # These derived integer buffers are deliberately absent from the weights.
        for block in self.blocks:
            rope = block.self_attention.rope
            rope.frequency_indices = torch.arange(0, rope.dim, 2, dtype=torch.int64, device="cpu")

    @apply_forward_hook
    def encode_midi(self, rows):
        return super().encode_midi(rows)

    @apply_forward_hook
    def prepare_clean(self, clean, observed):
        return super().prepare_clean(clean, observed)


class HeartCodecModel(ModelMixin, ConfigMixin):
    """The frozen Base SQ encoder and decoder, saved as one Diffusers component."""

    _no_split_modules = ["HeartCodecModel"]

    @register_to_config
    def __init__(self, codec_config: dict):
        super().__init__()
        config = ScalarModelConfig.from_obj(codec_config)
        if config.sample_rate != 48000 or config.latent_hidden_dim != 128 or not config.causal:
            raise ValueError("SpanSynth requires the causal 48 kHz, 128-channel Base SQ codec")
        self.model = ScalarModel(config)

    @apply_forward_hook
    def encode(self, waveform):
        return self.model.encode(waveform)

    def quantize(self, latents):
        return self.model.quantize(latents)

    @apply_forward_hook
    def decode(self, latents):
        return self.model.decode(latents)

    def forward(self, waveform):
        return self.model(waveform)
