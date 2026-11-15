from dataclasses import asdict, replace
from collections.abc import Callable
from time import perf_counter

import numpy as np

from stem_separator.application.instruments import REMAINDER
from stem_separator.application.stem_validation import inspect_stem
from stem_separator.application.types import Progress, ProgressCallback, SeparationError, SeparationRequest, SeparationResult
from stem_separator.audio.decode import decode_audio, has_signal
from stem_separator.backends.base import SeparationBackend
from stem_separator.storage.export import check_destination, export_stems


class SeparationService:
    def __init__(self, backend: SeparationBackend, ffmpeg: str | None = None) -> None:
        self._backend = backend
        self._ffmpeg = ffmpeg

    def run(self, request: SeparationRequest, progress: ProgressCallback, *, filter_empty: bool = False,
            check_cancel: Callable[[], None] | None = None) -> SeparationResult:
        started = perf_counter()
        source = request.source.resolve()
        destination = request.output_root.resolve() / request.source.stem
        check_destination(destination)

        spec = self._backend.spec
        progress(Progress("decode", f"Decoding {source.name} to {spec.sample_rate} Hz", model=spec.name))
        audio = decode_audio(source, spec.sample_rate, spec.channels, self._ffmpeg, check_cancel=check_cancel)
        frames = audio.shape[-1]
        if filter_empty and not has_signal(audio):
            raise SeparationError("音频接近静音，没有可分离的声音。")

        try:
            progress(Progress("load", f"Loading {spec.name} from local cache", model=spec.name,
                              duration=frames / spec.sample_rate))
            self._backend.load()
            result = self._backend.separate(audio, request.device, progress)
        finally:
            self._backend.unload()

        stems = result.stems
        progress(Progress("postprocess", "Aligning and checking stems", model=spec.name, device=result.device))
        if set(stems) != set(spec.stems):
            raise SeparationError("分离模型返回的音轨类别与预期不符。")
        diagnostics = {}
        mix_rms = float(np.einsum('ij,ij->', audio, audio, dtype=np.float64) / audio.size)**.5
        for name, stem in stems.items():
            if check_cancel:
                check_cancel()
            diagnostics[name] = inspect_stem(stem, audio, spec.sample_rate, mix_rms)
        notice = ""
        if filter_empty and spec.name != "Original audio":
            residual = REMAINDER if REMAINDER in stems else "other"
            empty = [name for name, data in diagnostics.items() if data.peak <= 2e-6]
            if len(empty) == len(stems):
                raise SeparationError("模型未输出可试听的音轨，请尝试其他模型。")
            if residual in stems:
                # Recompute the residual after validation. No source content or DC is lost;
                # the model's well-isolated stems keep their original levels and timing.
                for name in empty:
                    if name != residual:
                        stems.pop(name)
                remainder = audio.copy()
                for name, stem in stems.items():
                    if name != residual:
                        remainder -= stem
                diagnostics[residual] = inspect_stem(remainder, audio, spec.sample_rate, mix_rms)
                stems[residual] = remainder
                if len(stems) > 1 and not has_signal(remainder):
                    stems.pop(residual)
            if not any(name != residual and name not in empty for name in stems):
                notice = "模型已运行，但没有得到可靠的独立声部。可以尝试其他质量档或手动选轨。"
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
            diagnostics={name: asdict(data) for name, data in diagnostics.items()},
            validation_notice=notice,
        )
