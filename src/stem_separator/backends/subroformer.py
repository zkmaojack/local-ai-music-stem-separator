from time import perf_counter

import numpy as np
from safetensors.torch import load
import torch
import yaml

from stem_separator.application.instruments import STANDARD
from stem_separator.application.types import Device, Progress, ProgressCallback, Quality, SeparationError
from stem_separator.backends.base import Audio, InferenceResult, ModelSpec
from stem_separator.backends.demucs import device_label, resolve_device
from stem_separator.backends.subroformer_vendor.model import SBRoformer
from stem_separator.storage.model_cache import ModelCache


class SubRoformerBackend:
    spec = ModelSpec("SubRoformer", 48000, 2, STANDARD)

    def __init__(self, config: ModelCache, weights: ModelCache, quality: Quality) -> None:
        self.config = config
        self.weights = weights
        self.quality = quality
        self.model = None

    def load(self) -> None:
        with self.config.open_checkpoint() as file:
            config = yaml.safe_load(file)
        self.chunk = config["audio"]["chunk_size"]
        self.sources = config["training"]["instruments"]
        if set(self.sources) != set(STANDARD) or config["audio"]["sample_rate"] != self.spec.sample_rate:
            raise SeparationError("SubRoformer 模型配置与音轨格式不一致。")
        options = {key: value for key, value in config["model"].items() if key != "name"}
        model = SBRoformer(**options)
        with self.weights.open_checkpoint() as file:
            model.load_state_dict(load(file.read()), strict=True)
        self.model = model.eval()

    def unload(self) -> None:
        if self.model is not None:
            cuda = next(self.model.parameters()).is_cuda
            self.model = None
            if cuda:
                torch.cuda.empty_cache()

    def separate(self, audio: Audio, device: Device, progress: ProgressCallback) -> InferenceResult:
        started = perf_counter()
        actual = resolve_device(device)
        cuda = actual.type == "cuda"
        if cuda:
            torch.cuda.reset_peak_memory_stats(actual)
        try:
            stems = self._infer(audio, actual, progress)
        except torch.cuda.OutOfMemoryError:
            if not cuda:
                raise
            stems = None
        if stems is None:
            progress(Progress("device", "显存不足，正在缩短分块。"))
            torch.cuda.empty_cache()
            try:
                stems = self._infer(audio, actual, progress, chunk=self.chunk // 2)
            except torch.cuda.OutOfMemoryError:
                if device == "cuda":
                    raise SeparationError("显存不足，请切换质量档或改用 CPU。") from None
        allocated = torch.cuda.max_memory_allocated(actual) if cuda else None
        reserved = torch.cuda.max_memory_reserved(actual) if cuda else None
        if stems is None:
            progress(Progress("device", "显存不足，改用 CPU。"))
            actual = torch.device("cpu")
            self.model.to(actual)
            torch.cuda.empty_cache()
            stems = self._infer(audio, actual, progress)
        return InferenceResult(stems, device_label(actual), perf_counter() - started, allocated, reserved)

    def _infer(self, audio: Audio, device: torch.device, progress: ProgressCallback,
               *, chunk: int | None = None) -> dict[str, Audio]:
        model = self.model.to(device)
        frames = audio.shape[-1]
        chunk = chunk or self.chunk
        step = chunk // (4 if self.quality == "high" else 2)
        count = 1 + (max(0, frames - chunk) + step - 1) // step
        window = np.hamming(chunk).astype(np.float32)
        weight = np.zeros(frames, dtype=np.float32)
        output = np.zeros((len(self.sources), *audio.shape), dtype=np.float32)
        cuda = device.type == "cuda"
        torch.set_num_threads(min(torch.get_num_threads(), 4))
        label = device_label(device)
        progress(Progress("inference", label, 0, count, model=self.spec.name, device=label))
        with torch.inference_mode(), torch.autocast(device.type, dtype=torch.bfloat16, enabled=cuda and torch.cuda.is_bf16_supported()):
            for index in range(count):
                start = index * step
                stop = min(start + chunk, frames)
                length = stop - start
                block = torch.from_numpy(audio[:, start:stop]).unsqueeze(0).to(device)
                block = torch.nn.functional.pad(block, (0, chunk - length))
                estimate = model(block)[0, ..., :length].float().cpu().numpy()
                taper = window[:length]
                output[..., start:stop] += estimate * taper
                weight[start:stop] += taper
                progress(Progress("inference", label, index + 1, count, model=self.spec.name, device=label))
        np.divide(output, weight, out=output)
        stems = dict(zip(self.sources, output, strict=True))
        return {name: stems[name] for name in self.spec.stems}
