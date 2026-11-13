from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from stem_separator.application.types import Device, ProgressCallback

Audio = NDArray[np.float32]


@dataclass(frozen=True)
class ModelSpec:
    name: str
    sample_rate: int
    channels: int
    stems: tuple[str, ...]


@dataclass(frozen=True)
class InferenceResult:
    # Each stem has shape (channels, frames) at the model sample rate.
    stems: dict[str, Audio]
    device: str
    elapsed_seconds: float
    cuda_peak_allocated_bytes: int | None = None
    cuda_peak_reserved_bytes: int | None = None


class SeparationBackend(Protocol):
    spec: ModelSpec

    def load(self) -> None: ...

    def separate(
        self, audio: Audio, device: Device, progress: ProgressCallback
    ) -> InferenceResult: ...

    def unload(self) -> None: ...
