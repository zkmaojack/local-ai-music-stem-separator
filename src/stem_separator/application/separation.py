from dataclasses import replace
from collections.abc import Callable
from time import perf_counter

import numpy as np

from stem_separator.application.instruments import REMAINDER
from stem_separator.application.types import Progress, ProgressCallback, SeparationRequest, SeparationResult
from stem_separator.audio.decode import decode_audio, signal_profile
from stem_separator.backends.base import SeparationBackend
from stem_separator.storage.export import check_destination, export_stems


class SeparationService:
    def __init__(self, backend: SeparationBackend, ffmpeg: str | None = None) -> None:
        self._backend = backend
        self._ffmpeg = ffmpeg

    def run(self, request: SeparationRequest, progress: ProgressCallback, *, filter_empty: bool = False,
            include_signal: bool = False,
            check_cancel: Callable[[], None] | None = None) -> SeparationResult:
        started = perf_counter()
        source = request.source.resolve()
        destination = request.output_root.resolve() / request.source.stem
        check_destination(destination)

        spec = self._backend.spec
        progress(Progress("decode", f"Decoding {source.name} to {spec.sample_rate} Hz", model=spec.name))
        audio = decode_audio(source, spec.sample_rate, spec.channels, self._ffmpeg, check_cancel=check_cancel)
        frames = audio.shape[-1]

        try:
            profile = signal_profile(audio, spec.sample_rate) if include_signal else None
            progress(Progress("load", f"Loading {spec.name} from local cache", model=spec.name,
                              signal=profile, duration=frames / spec.sample_rate))
            self._backend.load()
            result = self._backend.separate(audio, request.device, progress)
        finally:
            self._backend.unload()

        stems = result.stems
        progress(Progress("postprocess", "Aligning and checking stems", model=spec.name))
        if filter_empty and REMAINDER in stems:
            floor = max(1e-5, float(np.max(np.abs(audio))) * .003)
            empty = [name for name, track in stems.items()
                     if name != REMAINDER and float(np.max(np.abs(track))) < floor]
            for name in empty:
                stems[REMAINDER] += stems.pop(name)
            spec = replace(spec, stems=tuple(name for name in spec.stems if name in stems))
        progress(Progress("export", f"Writing {len(spec.stems)} WAV files"))
        paths, gain = export_stems(destination, stems, spec, frames, check_cancel=check_cancel)
        return SeparationResult(
            paths=paths,
            model=spec.name,
            device=result.device,
            sample_rate=spec.sample_rate,
            frames=frames,
            elapsed_seconds=perf_counter() - started,
            inference_seconds=result.elapsed_seconds,
            cuda_peak_allocated_bytes=result.cuda_peak_allocated_bytes,
            cuda_peak_reserved_bytes=result.cuda_peak_reserved_bytes,
            export_gain=gain,
        )
