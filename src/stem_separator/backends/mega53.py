from contextlib import nullcontext
from time import perf_counter

import numpy as np
import torch
import yaml

from stem_separator.application.instruments import HEAD_IDS, REMAINDER, validate_selection
from stem_separator.application.types import Device, Progress, ProgressCallback, SeparationError
from stem_separator.backends.base import Audio, InferenceResult, ModelSpec
from stem_separator.backends.demucs import device_label, resolve_device
from stem_separator.backends.roformer.bs_roformer import BSRoformer
from stem_separator.storage.model_cache import ModelCache

RATE = 44100
MAX_HEADS_PER_PASS = 8


class Mega53Backend:
    def __init__(self, config: ModelCache, weights: ModelCache, targets: tuple[str, ...]) -> None:
        validate_selection(targets)
        if not targets:
            raise SeparationError("请选择至少一种乐器。")
        self.spec = ModelSpec("MVSep Mega 53", RATE, 2, (*targets, REMAINDER))
        self.targets = targets
        self.config = config
        self.weights = weights
        self.model: BSRoformer | None = None
        self.cuda_device: torch.device | None = None

    def load(self) -> None:
        with self.config.open_checkpoint() as file:
            config = yaml.safe_load(file.read().replace(b"!!python/tuple", b""))
        if config["training"]["instruments"] != list(HEAD_IDS):
            raise SeparationError("模型音轨顺序与应用的乐器清单不一致。")
        with self.weights.open_checkpoint() as file:
            state = torch.load(file, map_location="cpu", weights_only=True)
        remap = {str(HEAD_IDS[name]): index for index, name in enumerate(self.targets)}
        selected = {}
        for key, tensor in state.items():
            prefix, _, tail = key.partition(".")
            if prefix != "mask_estimators":
                selected[key] = tensor
                continue
            head, _, name = tail.partition(".")
            if head in remap:
                selected[f"mask_estimators.{remap[head]}.{name}"] = tensor
        del state
        options = config["model"]
        for key in ("freqs_per_bands", "multi_stft_resolutions_window_sizes"):
            options[key] = tuple(options[key])
        options["num_stems"] = len(self.targets)
        model = BSRoformer(**options)
        model.load_state_dict(selected)
        self.model = model.eval()

    def unload(self) -> None:
        self.model = None
        if self.cuda_device is not None:
            torch.cuda.empty_cache()
            self.cuda_device = None

    def separate(self, audio: Audio, device: Device, progress: ProgressCallback) -> InferenceResult:
        model = self.model
        if model is None:
            raise SeparationError("请先加载乐器模型。")
        start = perf_counter()
        actual = resolve_device(device)
        allocated = reserved = None
        if actual.type == "cuda":
            self.cuda_device = actual
            torch.cuda.reset_peak_memory_stats(actual)
            stems = self._cuda(audio, actual, progress)
            allocated = torch.cuda.max_memory_allocated(actual)
            reserved = torch.cuda.max_memory_reserved(actual)
            if stems is None:
                if device == "cuda":
                    raise SeparationError("显存不足，请减少选中的乐器或改用 CPU。")
                progress(Progress("device", "显存不足，改用 CPU。"))
                actual = torch.device("cpu")
                model.to(actual)
                torch.cuda.empty_cache()
                stems = self._infer(audio, actual, 3, progress)
        else:
            stems = self._infer(audio, actual, 3, progress)
        remainder = audio.copy()
        for samples in stems.values():
            np.subtract(remainder, samples, out=remainder)
        stems[REMAINDER] = remainder
        return InferenceResult(stems, device_label(actual), perf_counter() - start, allocated, reserved)

    def _cuda(self, audio: Audio, device: torch.device, progress: ProgressCallback) -> dict[str, Audio] | None:
        for seconds in (6, 3, 1.5):
            try:
                return self._infer(audio, device, seconds, progress)
            except torch.cuda.OutOfMemoryError:
                pass
            torch.cuda.empty_cache()
            progress(Progress("device", "显存不足，正在缩短分块。"))
        return None

    def _infer(self, audio: Audio, device: torch.device, seconds: float,
               progress: ProgressCallback) -> dict[str, Audio]:
        model = self.model
        if model is None:
            raise SeparationError("请先加载乐器模型。")
        cuda = device.type == "cuda"
        mixed_precision = cuda and torch.cuda.is_bf16_supported(including_emulation=False)
        # Unused heads stay on the CPU. The encoder and one group of heads fit together on the GPU.
        heads = model.mask_estimators
        if cuda:
            for name, layer in model.named_children():
                if name != "mask_estimators":
                    layer.to(device)
        groups = [list(range(index, min(index + MAX_HEADS_PER_PASS, len(self.targets))))
                  for index in range(0, len(self.targets), MAX_HEADS_PER_PASS)]
        chunk = round(seconds * RATE)
        hop = chunk * 3 // 4
        frames = audio.shape[-1]
        blocks = 1 + (max(0, frames - chunk) + hop - 1) // hop
        offsets = range(0, blocks * hop, hop)
        taper = np.ones(chunk, dtype=np.float32)
        fade = np.linspace(0, 1, chunk - hop + 2, dtype=np.float32)[1:-1]
        taper[:len(fade)], taper[-len(fade):] = fade, fade[::-1]
        envelope = np.zeros(frames, dtype=np.float32)
        for offset in offsets:
            count = min(chunk, frames - offset)
            envelope[offset:offset + count] += taper[:count]

        output = {name: np.zeros_like(audio) for name in self.targets}
        total = len(groups) * len(offsets)
        done = 0
        label = device_label(device)
        progress(Progress("inference", label, done, total))
        with torch.inference_mode():
            for ids in groups:
                for index in ids:
                    heads[index].to(device)
                try:
                    for offset in offsets:
                        count = min(chunk, frames - offset)
                        mix = torch.from_numpy(audio[:, offset:offset + count]).unsqueeze(0).to(device)
                        if count < chunk:
                            mix = torch.nn.functional.pad(mix, (0, chunk - count))
                        with torch.autocast("cuda", dtype=torch.bfloat16) if mixed_precision else nullcontext():
                            prediction = model(mix, active_stem_ids=ids)[0, :, :, :count].cpu().numpy()
                        for index, estimate in zip(ids, prediction, strict=True):
                            target = output[self.targets[index]][:, offset:offset + count]
                            target += estimate * taper[:count]
                        done += 1
                        progress(Progress("inference", label, done, total))
                finally:
                    if cuda:
                        for index in ids:
                            heads[index].cpu()
        for stem in output.values():
            np.divide(stem, envelope, out=stem)
        return output
