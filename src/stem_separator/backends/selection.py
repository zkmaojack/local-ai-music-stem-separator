from dataclasses import replace
from pathlib import Path

from stem_separator.application.instruments import (
    EFFECT_PROMPTS, FOUR_STEM_TARGETS, HEAD_IDS, REMAINDER, STANDARD, validate_model_selection,
)
from stem_separator.application.types import Device, ModelChoice, Progress, ProgressCallback, Quality
from stem_separator.backends.base import Audio, InferenceResult, ModelSpec, SeparationBackend
from stem_separator.storage.model_cache import ModelCache, effect_assets, model_assets, subroformer_assets, uses_standard_model


def build_backend(root: Path, targets: tuple[str, ...], quality: Quality | None = None,
                  model: ModelChoice = "auto") -> SeparationBackend:
    if targets == (REMAINDER,):
        return RemainderBackend()
    validate_model_selection(targets, model)
    if uses_standard_model(targets, quality, model):
        if model == "demucs" or (model == "auto" and quality in (None, "fast")):
            from stem_separator.backends.demucs import DemucsBackend

            backend = DemucsBackend(ModelCache(root), compact=bool(targets))
        else:
            from stem_separator.backends.subroformer import SubRoformerBackend

            backend = SubRoformerBackend(*subroformer_assets(root), quality or "balanced")
            if quality == "high":
                tuned = SubRoformerBackend(*subroformer_assets(root, tuned=True), quality)
                backend = FourStemEnsemble(backend, tuned)
        return SelectedFourStems(backend, targets) if targets else backend

    instrument_ids = tuple(name for name in targets if name in HEAD_IDS)
    effect_ids = tuple(name for name in targets if name in EFFECT_PROMPTS)
    backends = []
    if instrument_ids:
        from stem_separator.backends.mega53 import Mega53Backend

        backends.append(Mega53Backend(*model_assets(root, True), instrument_ids, quality=quality or "balanced"))
    if effect_ids:
        from stem_separator.backends.audiosep.effects import EffectsBackend

        backends.append(EffectsBackend(effect_assets(root), effect_ids, quality or "balanced"))
    return backends[0] if len(backends) == 1 else CombinedBackend(backends, targets)


class RemainderBackend:
    spec = ModelSpec("Original audio", 44100, 2, (REMAINDER,))

    def load(self) -> None:
        pass

    def unload(self) -> None:
        pass

    def separate(self, audio: Audio, device: Device, progress: ProgressCallback) -> InferenceResult:
        return InferenceResult({REMAINDER: audio}, "CPU", 0.)


class SelectedFourStems:
    def __init__(self, backend: SeparationBackend, targets: tuple[str, ...]) -> None:
        self.backend = backend
        self.spec = replace(backend.spec, stems=(*targets, REMAINDER))

    def load(self) -> None:
        self.backend.load()

    def unload(self) -> None:
        self.backend.unload()

    def separate(self, audio: Audio, device: Device, progress: ProgressCallback) -> InferenceResult:
        result = self.backend.separate(audio, device, progress)
        chosen = {name: result.stems[FOUR_STEM_TARGETS[name]] for name in self.spec.stems if name != REMAINDER}
        remainder = audio.copy()
        for track in chosen.values():
            remainder -= track
        return replace(result, stems=chosen | {REMAINDER: remainder})


class FourStemEnsemble:
    spec = ModelSpec("SubRoformer ensemble", 48000, 2, STANDARD)

    def __init__(self, base: SeparationBackend, tuned: SeparationBackend) -> None:
        self.base = base
        self.tuned = tuned

    def load(self) -> None:
        self.base.load()

    def unload(self) -> None:
        self.base.unload()
        self.tuned.unload()

    def separate(self, audio: Audio, device: Device, progress: ProgressCallback) -> InferenceResult:
        try:
            a = self.base.separate(audio, device, progress)
        finally:
            self.base.unload()
        progress(Progress("load", "正在加载第二个分离模型…", model=self.tuned.spec.name))
        self.tuned.load()
        try:
            b = self.tuned.separate(audio, device, progress)
        finally:
            self.tuned.unload()
        for stem in self.spec.stems:
            a.stems[stem] += b.stems[stem]
            a.stems[stem] *= .5
        return replace(a, elapsed_seconds=a.elapsed_seconds + b.elapsed_seconds,
                       device=" / ".join(dict.fromkeys((a.device, b.device))),
                       cuda_peak_allocated_bytes=max(a.cuda_peak_allocated_bytes or 0, b.cuda_peak_allocated_bytes or 0) or None,
                       cuda_peak_reserved_bytes=max(a.cuda_peak_reserved_bytes or 0, b.cuda_peak_reserved_bytes or 0) or None)


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
                progress(Progress("load", "正在加载音效模型…", model=backend.spec.name))
                backend.load()

            try:
                result = backend.separate(audio, device, progress)
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
