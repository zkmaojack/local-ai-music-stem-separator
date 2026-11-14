from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

import numpy as np
import soundfile as sf

from stem_separator.application.types import SeparationError
from stem_separator.backends.base import Audio, ModelSpec

MAX_PCM_24 = 1.0 - 2.0**-23


def check_destination(destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise SeparationError(f"Output already exists: {destination}. Choose another output directory.")


@contextmanager
def _stage_export(destination: Path) -> Iterator[Path]:
    check_destination(destination)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".stems-", dir=destination.parent) as temp:
            staging = Path(temp) / "stems"
            staging.mkdir()
            yield staging
            staging.rename(destination)
    except (OSError, sf.SoundFileError) as error:
        raise SeparationError(f"Could not export to {destination}: {error}") from error


def copy_stems(destination: Path, selected: list[Path]) -> Path:
    with _stage_export(destination) as staging:
        for path in selected:
            copyfile(path, staging / path.name)
    return destination


def export_stems(
    destination: Path, stems: dict[str, Audio], spec: ModelSpec, frames: int,
    *, check_cancel: Callable[[], None] | None = None,
) -> tuple[dict[str, Path], float]:
    check_destination(destination)
    if set(stems) != set(spec.stems):
        raise SeparationError("The model returned an unexpected set of stems.")
    peak = 0.0
    for name, samples in stems.items():
        if check_cancel is not None:
            check_cancel()
        if samples.shape != (spec.channels, frames) or not np.isfinite(samples).all():
            raise SeparationError(f"The {name} stem has an invalid shape or non-finite samples.")
        peak = max(peak, float(samples.max()), -float(samples.min()))
    gain = min(1.0, MAX_PCM_24 / peak) if peak else 1.0

    with _stage_export(destination) as staging:
        for name in spec.stems:
            if check_cancel is not None:
                check_cancel()
            samples = stems[name] if gain == 1.0 else stems[name] * gain
            sf.write(staging / f"{name}.wav", samples.T, spec.sample_rate, subtype="PCM_24")
        if check_cancel is not None:
            check_cancel()
    return {name: destination / f"{name}.wav" for name in spec.stems}, gain
