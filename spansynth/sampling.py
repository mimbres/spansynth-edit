"""Euler generation and FlowEdit in normalized scalar-quantized latent space."""
from __future__ import annotations
from collections.abc import Callable
import math
import torch


def _without_generated_notes(rows, generated):
    keep = rows["event_valid"].clone()
    for batch in range(keep.shape[0]):
        selected = rows["physical_note_id"][batch][keep[batch] & generated[batch, :, None]]
        keep[batch] &= ~torch.isin(rows["physical_note_id"][batch], selected.unique())
    result = {}
    for key, value in rows.items():
        mask = keep[..., None] if key == "numeric" else keep
        fill = -1 if key in ("category_id", "physical_note_id") else 0
        result[key] = value.masked_fill(~mask, fill)
    result["event_valid"] = keep
    return result


def _cat_rows(groups):
    return {key: torch.cat([group[key] for group in groups]) for key in groups[0]}


@torch.inference_mode()
def sample(model, codes, target_rows, observed, *, source_rows=None, method="ordinary",
           steps=16, cfg=2.0, context_midi=False, drop_context_audio=False,
           start_step=0, average=1, progress: Callable | None = None, noise=None):
    """Return integer codes. Every observed code remains exactly unchanged.

    ``noise`` supports numerical parity tests of Euler generation. Public runs
    draw fresh randomness from PyTorch's default generator.
    """
    if method not in ("ordinary", "flowedit"):
        raise ValueError("method must be ordinary or flowedit")
    if steps < 1 or not math.isfinite(cfg) or cfg < 0 or average < 1:
        raise ValueError("steps and average must be positive, CFG finite and non-negative")
    if not 0 <= start_step < steps:
        raise ValueError("FlowEdit start step must be in [0, steps)")
    if method == "flowedit" and source_rows is None:
        raise ValueError("FlowEdit requires source MIDI rows")
    if codes.ndim != 3 or codes.shape[1:] != (512, 128):
        raise ValueError("codes must have shape [batch,512,128]")
    if observed.shape != codes.shape[:2] or observed.dtype != torch.bool:
        raise ValueError("observed must be a boolean [batch,512] mask")
    if not (~observed).any(dim=1).all():
        raise ValueError("Every input must contain a generated interval")
    if (codes < -9).any() or (codes > 9).any():
        raise ValueError("SQ codes must lie in [-9,9]")
    model.eval()
    clean = codes.float() / 9.0
    generated = ~observed
    availability = observed if context_midi else torch.zeros_like(observed)
    groups = [target_rows]
    if cfg != 1:
        groups.append(_without_generated_notes(target_rows, generated))
    if method == "flowedit":
        groups.append(source_rows)
        if cfg != 1:
            groups.append(_without_generated_notes(source_rows, generated))
    branches = len(groups)
    if method == "ordinary":
        average = 1
    groups = [{k: v.repeat(average, *([1] * (v.ndim - 1))) for k, v in group.items()} for group in groups]
    branch_observed = observed.repeat(branches * average, 1)
    branch_available = availability.repeat(branches * average, 1)
    condition = model.encode_midi(_cat_rows(groups))
    reference = torch.zeros_like(clean) if drop_context_audio else clean
    clean_hidden = model.prepare_clean(reference.repeat(branches * average, 1, 1), branch_observed)
    def predict(state, time):
        return model(state, time, branch_observed, condition, branch_available, clean_hidden).float()
    if method == "ordinary":
        initial = torch.randn_like(clean) if noise is None else noise.float()
        if initial.shape != clean.shape or not torch.isfinite(initial).all():
            raise ValueError("noise must be finite and match the latent shape")
        state = initial.clone()
        for step in range(steps):
            left, right = step / steps, (step + 1) / steps
            model_state = torch.where(observed[..., None], (1 - left) * initial + left * clean, state)
            time = torch.full((clean.shape[0] * branches,), left, device=codes.device)
            velocities = predict(model_state.repeat(branches, 1, 1), time)
            if cfg == 1:
                velocity = velocities
            else:
                full, off = velocities.chunk(2)
                velocity = off + cfg * (full - off)
            state = torch.where(generated[..., None], model_state + velocity / steps,
                                (1 - right) * initial + right * clean)
            if progress:
                progress(step + 1, steps)
    else:
        state = clean.clone()
        repeated_clean = clean.repeat(average, 1, 1)
        grid = torch.linspace(0, 1, steps + 1, device=codes.device, dtype=torch.float32)
        for step in range(start_step, steps):
            left, right = grid[step], grid[step + 1]
            fresh = torch.randn_like(repeated_clean)
            source_flow = (1 - left) * fresh + left * repeated_clean
            target_flow = source_flow + (state.repeat(average, 1, 1) - repeated_clean)
            target_flow = torch.where(observed.repeat(average, 1)[..., None], source_flow, target_flow)
            states = [target_flow, source_flow] if cfg == 1 else [target_flow, target_flow, source_flow, source_flow]
            stacked = torch.cat(states)
            velocities = predict(stacked, left.expand(stacked.shape[0]))
            if cfg == 1:
                target_v, source_v = velocities.chunk(2)
            else:
                target_full, target_off, source_full, source_off = velocities.chunk(4)
                target_v = target_off + cfg * (target_full - target_off)
                source_v = source_off + cfg * (source_full - source_off)
            delta = (target_v - source_v).reshape(average, *clean.shape).mean(0)
            state = torch.where(generated[..., None], state + (right - left) * delta, clean)
            if progress:
                progress(step - start_step + 1, steps - start_step)
    if not torch.isfinite(state).all():
        raise RuntimeError("Inference produced non-finite latents")
    result = torch.round(state * 9).clamp(-9, 9).to(torch.int8)
    return torch.where(observed[..., None], codes.to(torch.int8), result)
