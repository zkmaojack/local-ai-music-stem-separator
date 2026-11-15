from dataclasses import dataclass

import numpy as np

from stem_separator.application.types import SeparationError
from stem_separator.application.instruments import FOUR_STEM_TARGETS
from stem_separator.backends.base import Audio


@dataclass(frozen=True)
class StemDiagnostics:
    peak: float
    rms: float
    silent_block_ratio: float
    clipped_samples: int
    active_level_span_db: float
    mix_similarity: float
    rms_to_mix: float


def quiet_unconfirmed_stems(diagnostics: dict, recognized: tuple[str, ...]) -> tuple[str, ...]:
    """Suggest hiding weak broad estimates with no supporting input detection.

    Keep these stems in the session and export list. A quiet instrument or a short
    entrance can be real, so this rule must never delete its waveform.
    """
    confirmed = {FOUR_STEM_TARGETS.get(name, name) for name in recognized}
    limits = {"vocals": .04, "drums": .025, "bass": .025}
    return tuple(name for name, limit in limits.items()
                 if name in diagnostics and name not in confirmed and diagnostics[name]["rms_to_mix"] < limit)


def inspect_stem(stem: Audio, mix: Audio, rate: int, mix_rms: float) -> StemDiagnostics:
    if stem.shape != mix.shape or not np.isfinite(stem).all():
        raise SeparationError("分离模型返回了长度、声道或采样值不正确的音轨。")
    peak = max(float(stem.max()), -float(stem.min()))
    energies = []
    clipped = 0
    for start in range(0, stem.shape[-1], max(1, rate // 5)):
        block = stem[:, start:start + max(1, rate // 5)]
        energies.append((block.size, float(np.einsum('ij,ij->', block, block, dtype=np.float64))))
        clipped += int(np.count_nonzero(np.abs(block) >= 1.0))
    levels = np.array([(energy / count)**.5 for count, energy in energies])
    active = levels[levels > 2e-6]
    span = float(20 * np.log10(np.percentile(active, 95) / np.percentile(active, 5))) if len(active) > 1 else 0.
    # Similarity is a diagnostic for listening, not proof of leakage or wrong labels.
    stride = max(1, stem.shape[-1] // 20_000)
    a, b = (signal[:, ::stride].ravel().astype(np.float64) for signal in (stem, mix))
    scale = np.linalg.norm(a) * np.linalg.norm(b)
    rms = (sum(energy for _, energy in energies) / stem.size)**.5
    return StemDiagnostics(peak, rms,
                           float(np.mean(levels <= 2e-6)), clipped, span,
                           float(abs(np.dot(a, b)) / scale) if scale else 0., rms / max(mix_rms, 1e-12))
