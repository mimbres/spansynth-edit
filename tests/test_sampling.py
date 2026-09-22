import numpy as np
import torch
from torch import nn
from spansynth.midi import NOTE_DTYPE, prepare_midi
from spansynth.sampling import sample
from spansynth.audio import assemble_output


class ConstantModel(nn.Module):
    def encode_midi(self, rows):
        return rows["event_valid"].any(-1)[..., None].float()

    def prepare_clean(self, clean, observed):
        return clean.masked_fill(~observed[..., None], 0)

    def forward(self, state, time, observed, midi, availability, clean):
        return midi.expand_as(state) * 0.1


def inputs():
    codes = torch.randint(-9, 10, (1, 512, 128), dtype=torch.int8)
    generated = torch.zeros(512, dtype=torch.bool)
    generated[160:352] = True
    notes = np.array([(160 * 1920, 352 * 1920, 60, 90, 0, 0)], dtype=NOTE_DTYPE)
    empty = np.zeros(0, dtype=NOTE_DTYPE)
    target = prepare_midi(notes, empty, generated)
    source = prepare_midi(empty, empty, generated)
    return codes, generated, target, source


def test_euler_cfg_and_observed_codes():
    codes, generated, target, _ = inputs()
    output = sample(ConstantModel(), codes, target, (~generated)[None], steps=4, cfg=2,
                    noise=torch.zeros_like(codes, dtype=torch.float32))
    assert torch.equal(output[:, ~generated], codes[:, ~generated])
    assert (output[:, generated] == 2).all()


def test_flowedit_difference_and_equal_midi_noop():
    codes, generated, target, source = inputs()
    output = sample(ConstantModel(), codes, target, (~generated)[None], source_rows=source,
                    method="flowedit", steps=4, cfg=2, average=2)
    expected = (codes[:, generated].float() + 1.8).round().clamp(-9, 9).to(torch.int8)
    assert torch.equal(output[:, generated], expected)
    assert torch.equal(output[:, ~generated], codes[:, ~generated])
    unchanged = sample(ConstantModel(), codes, target, (~generated)[None], source_rows=target,
                       method="flowedit", steps=4)
    assert torch.equal(unchanged, codes)


def test_output_preserves_samples_outside_interval():
    original = np.random.default_rng().normal(0, 0.1, 48000).astype(np.float32)
    decoded = np.full(48000, 0.2, dtype=np.float32)
    result = assemble_output(original, decoded, 0.5, 10000, 30000)
    np.testing.assert_array_equal(result[:10000], original[:10000])
    np.testing.assert_array_equal(result[30000:], original[30000:])
    np.testing.assert_array_equal(result[10000:30000], np.full(20000, 0.4, dtype=np.float32))
