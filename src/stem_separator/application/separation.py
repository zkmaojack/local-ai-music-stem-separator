from time import perf_counter

from stem_separator.application.types import Progress, ProgressCallback, SeparationRequest, SeparationResult
from stem_separator.audio.decode import decode_audio
from stem_separator.backends.base import SeparationBackend
from stem_separator.storage.export import check_destination, export_stems


class SeparationService:
    def __init__(self, backend: SeparationBackend, ffmpeg: str | None = None) -> None:
        self._backend = backend
        self._ffmpeg = ffmpeg

    def run(self, request: SeparationRequest, progress: ProgressCallback) -> SeparationResult:
        started = perf_counter()
        source = request.source.resolve()
        destination = request.output_root.resolve() / request.source.stem
        check_destination(destination)

        spec = self._backend.spec
        progress(Progress("decode", f"Decoding {source.name} to {spec.sample_rate} Hz"))
        audio = decode_audio(source, spec.sample_rate, spec.channels, self._ffmpeg)
        frames = audio.shape[-1]

        try:
            progress(Progress("load", f"Loading {spec.name} from local cache"))
            self._backend.load()
            result = self._backend.separate(audio, request.device, progress)
        finally:
            self._backend.unload()

        progress(Progress("export", f"Writing {len(spec.stems)} WAV files"))
        paths, gain = export_stems(destination, result.stems, spec, frames)
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
