"""Audio loading, causal codec history, and sample-exact output assembly."""
from dataclasses import dataclass
from pathlib import Path
import math
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
import torch

SAMPLE_RATE = 48_000
HOP_LENGTH = 1920
FRAMES = 512
PAYLOAD_SAMPLES = FRAMES * HOP_LENGTH
HISTORY_FRAMES = 50
HISTORY_SAMPLES = HISTORY_FRAMES * HOP_LENGTH


@dataclass
class AudioCrop:
    waveform: torch.Tensor
    original: np.ndarray
    gain: float
    input_rate: int
    input_channels: int
    padded_samples: int


def read_audio(path: Path, crop_start: float, duration: float) -> AudioCrop:
    values, rate = sf.read(path, dtype="float32", always_2d=True)
    channels = values.shape[1]
    if not values.size or not np.isfinite(values).all():
        raise ValueError("Input audio must be nonempty and finite")
    mono = values.mean(axis=1)
    if rate != SAMPLE_RATE:
        divisor = math.gcd(rate, SAMPLE_RATE)
        mono = resample_poly(mono, SAMPLE_RATE // divisor, rate // divisor).astype(np.float32)
    start = math.floor(crop_start * SAMPLE_RATE + 0.5)
    count = math.floor(duration * SAMPLE_RATE + 0.5)
    if start >= len(mono):
        raise ValueError("crop start is beyond the end of the audio")
    original = np.zeros(count, dtype=np.float32)
    available = min(count, len(mono) - start)
    original[:available] = mono[start:start + available]
    history = np.zeros(HISTORY_SAMPLES + PAYLOAD_SAMPLES, dtype=np.float32)
    read_start = max(0, start - HISTORY_SAMPLES)
    left = max(0, HISTORY_SAMPLES - start)
    selected = mono[read_start:min(len(mono), start + count)]
    history[left:left + len(selected)] = selected
    peak = float(np.abs(history).max())
    gain = 0.95 / peak if peak > 0.95 else 1.0
    return AudioCrop(torch.from_numpy(history * gain), original, gain, rate, channels, count - available)


@torch.inference_mode()
def encode_audio(codec, crop, device):
    dtype = next(codec.parameters()).dtype
    encoded = codec.quantize(codec.encode(crop.waveform[None, None].to(device=device, dtype=dtype)))
    if encoded.shape != (1, 128, FRAMES + HISTORY_FRAMES):
        raise RuntimeError(f"Unexpected SQ encoder shape: {tuple(encoded.shape)}")
    return (encoded * 9).round().clamp(-9, 9).to(torch.int8).transpose(1, 2).contiguous()


@torch.inference_mode()
def decode_audio(codec, codes, history, device):
    dtype = next(codec.parameters()).dtype
    combined = torch.cat((history, codes), dim=1)
    latent = combined.transpose(1, 2).to(device=device, dtype=dtype) / 9
    decoded = codec.decode(latent).float().flatten().cpu().numpy()
    if len(decoded) != HISTORY_SAMPLES + PAYLOAD_SAMPLES or not np.isfinite(decoded).all():
        raise RuntimeError("SQ decoder returned an invalid waveform")
    return decoded[HISTORY_SAMPLES:]


def assemble_output(original, decoded, gain, start_sample, end_sample):
    """Only replace the requested interval on the resampled mono input timeline."""
    if not 0 <= start_sample < end_sample <= len(original):
        raise ValueError("Output interval must lie inside the crop")
    result = original.copy()
    result[start_sample:end_sample] = decoded[start_sample:end_sample] / gain
    return result
