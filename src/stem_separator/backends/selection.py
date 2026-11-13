from pathlib import Path

from stem_separator.application.instruments import EFFECT_PROMPTS, HEAD_IDS, REMAINDER, validate_selection
from stem_separator.application.types import Device, Progress, ProgressCallback
from stem_separator.backends.base import Audio, InferenceResult, ModelSpec, SeparationBackend
from stem_separator.storage.model_cache import ModelCache, effect_assets, model_assets


def build_backend(root: Path, targets: tuple[str, ...]) -> SeparationBackend:
    validate_selection(targets)
    if not targets:
        from stem_separator.backends.demucs import DemucsBackend

        return DemucsBackend(ModelCache(root))

    instrument_ids = tuple(name for name in targets if name in HEAD_IDS)
    effect_ids = tuple(name for name in targets if name in EFFECT_PROMPTS)
    backends = []
    if instrument_ids:
        from stem_separator.backends.mega53 import Mega53Backend

        backends.append(Mega53Backend(*model_assets(root, True), instrument_ids))
    if effect_ids:
        from stem_separator.backends.audiosep.effects import EffectsBackend

        backends.append(EffectsBackend(effect_assets(root), effect_ids))
    return backends[0] if len(backends) == 1 else CombinedBackend(backends, targets)


class CombinedBackend:
    def __init__(self, backends: list[SeparationBackend], targets: tuple[str, ...]) -> None:
        self.backends = backends
        self.spec = ModelSpec("MVSep Mega 53 + AudioSep", 44100, 2, (*targets, REMAINDER))

    def load(self) -> None:
        self.backends[0].load()

    def unload(self) -> None:
        for backend in self.backends:
            backend.unload()

    def separate(self, audio: Audio, device: Device, progress: ProgressCallback) -> InferenceResult:
        results = []
        stems = {}
        for index, backend in enumerate(self.backends):
            if index:
                progress(Progress("load", "正在加载音效模型…"))
                backend.load()

            def report(event: Progress) -> None:
                if event.total:
                    done = index * 100 + (event.completed or 0) * 100 // event.total
                    event = Progress(event.stage, event.message, done, len(self.backends) * 100)
                progress(event)

            try:
                result = backend.separate(audio, device, report)
            finally:
                backend.unload()
            results.append(result)
            audio = result.stems.pop(REMAINDER)
            stems.update(result.stems)
        stems[REMAINDER] = audio
        allocated = max((item.cuda_peak_allocated_bytes for item in results if item.cuda_peak_allocated_bytes is not None), default=None)
        reserved = max((item.cuda_peak_reserved_bytes for item in results if item.cuda_peak_reserved_bytes is not None), default=None)
        devices = " / ".join(dict.fromkeys(item.device for item in results))
        return InferenceResult(stems, devices, sum(item.elapsed_seconds for item in results), allocated, reserved)
