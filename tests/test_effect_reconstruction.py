import numpy as np
import pytest
import torch

from stem_separator.backends.audiosep.effects import stitch_effect_chunk


def reconstruct(source: torch.Tensor, body: int, context: int, gain_by_chunk=None) -> torch.Tensor:
    padded = torch.nn.functional.pad(source, (context, context))
    output = torch.empty_like(source)
    fade = .5 - .5 * torch.cos(torch.pi * torch.linspace(0, 1, 16))
    for index, offset in enumerate(range(0, source.shape[-1], body)):
        estimate = padded[:, offset:offset + body + 2 * context]
        if gain_by_chunk is not None:
            estimate = estimate * gain_by_chunk[index % len(gain_by_chunk)]
        stitch_effect_chunk(output, estimate, offset, body, context, fade)
    return output


@pytest.mark.parametrize("frames", [4, 32, 33, 97, 643])
def test_stitch_preserves_stereo_level_dynamics_and_timing(frames) -> None:
    signal = torch.stack((torch.full((frames,), .2), torch.full((frames,), -.05)))
    signal[:, frames // 4:frames // 2] *= .0001
    signal[:, ::32] += .45
    torch.testing.assert_close(reconstruct(signal, 32, 12), signal)


def test_blending_smooths_model_gain_disagreement_only_at_seams() -> None:
    signal = torch.ones(2, 96) * .2
    actual = reconstruct(signal, 32, 12, (.5, 1.))
    hard_join = signal * torch.tensor([.5] * 32 + [1.] * 32 + [.5] * 32)
    assert np.max(np.abs(np.diff(actual.numpy()))) < np.max(np.abs(np.diff(hard_join.numpy()))) / 7
    torch.testing.assert_close(actual[:, :24], hard_join[:, :24])
    torch.testing.assert_close(actual[:, 40:56], hard_join[:, 40:56])
    torch.testing.assert_close(actual[:, 72:], hard_join[:, 72:])
