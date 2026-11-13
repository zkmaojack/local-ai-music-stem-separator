from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Device = Literal["auto", "cuda", "cpu"]


class SeparationError(Exception):
    """An actionable failure that can be shown to the user."""


@dataclass(frozen=True)
class Progress:
    stage: str
    message: str
    completed: int | None = None
    total: int | None = None


ProgressCallback = Callable[[Progress], None]


@dataclass(frozen=True)
class SeparationRequest:
    source: Path
    output_root: Path
    device: Device = "auto"


@dataclass(frozen=True)
class SeparationResult:
    paths: dict[str, Path]
    model: str
    device: str
    sample_rate: int
    frames: int
    elapsed_seconds: float
    inference_seconds: float
    cuda_peak_allocated_bytes: int | None
    cuda_peak_reserved_bytes: int | None
    export_gain: float
