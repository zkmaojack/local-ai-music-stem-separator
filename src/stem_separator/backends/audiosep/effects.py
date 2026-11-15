from contextlib import nullcontext
from time import perf_counter

import numpy as np
import torch
from torch import nn
from torchaudio.functional import resample

from stem_separator.application.instruments import EFFECT_PROMPTS, REMAINDER
from stem_separator.application.types import Device, Progress, ProgressCallback, Quality, SeparationError
from stem_separator.backends.base import Audio, InferenceResult, ModelSpec
from stem_separator.backends.demucs import device_label, resolve_device
from stem_separator.storage.model_cache import ModelCache

MODEL_RATE = 32000
EXPORT_RATE = 44100
SEAM_FADE_SECONDS = .16


def stitch_effect_chunk(output: torch.Tensor, estimate: torch.Tensor, offset: int, body: int, context: int,
                        fade_in: torch.Tensor) -> None:
    """Blend neighboring estimates of the same frames using their existing extra context."""
    half = fade_in.numel() // 2
    start = max(0, offset - half)
    end = min(output.shape[-1], offset + body + half)
    first = context + start - offset
    # Transfer only the model frames used for this part of the output.
    block = estimate[:, first:first + end - start].float().cpu()
    if offset:
        count = min(fade_in.numel(), end - start)
        target = output[:, start:start + count]
        target.lerp_(block[:, :count], fade_in[:count])
        start += count
        block = block[:, count:]
    output[:, start:end] = block


class EffectsBackend:
    def __init__(self, assets: tuple[ModelCache, ...], targets: tuple[str, ...], quality: Quality = "balanced") -> None:
        self.assets = assets
        self.targets = targets
        self.quality = quality
        self.spec = ModelSpec("AudioSep", EXPORT_RATE, 2, (*targets, REMAINDER))
        self.model: nn.Module | None = None
        self.conditions: torch.Tensor | None = None
        self.used_cuda = False

    def load(self) -> None:
        from transformers import RobertaConfig, RobertaModel, RobertaTokenizer

        from .resunet import ResUNet30

        weights, vocab, merges = self.assets
        with weights.open_checkpoint() as file:
            state = torch.load(file, map_location="cpu", weights_only=True)
        with vocab.open_checkpoint(), merges.open_checkpoint():
            tokenizer = RobertaTokenizer(vocab_file=str(vocab.path), merges_file=str(merges.path))
        # The checkpoint already includes the full text encoder. No pretrained lookup is needed.
        encoder = RobertaModel(RobertaConfig(vocab_size=50265, max_position_embeddings=514,
                                             type_vocab_size=1, pad_token_id=1)).eval()
        projection = nn.Sequential(nn.Linear(768, 512), nn.ReLU(), nn.Linear(512, 512)).eval()
        prefix = "query_encoder.model.text_branch."
        encoder.load_state_dict({key.removeprefix(prefix): tensor for key, tensor in state.items()
                                 if key.startswith(prefix) and not key.endswith(".position_ids")})
        prefix = "query_encoder.model.text_projection."
        projection.load_state_dict({key.removeprefix(prefix): tensor for key, tensor in state.items()
                                    if key.startswith(prefix)})
        with torch.inference_mode():
            batch = tokenizer([EFFECT_PROMPTS[target] for target in self.targets], padding=True, return_tensors="pt")
            self.conditions = nn.functional.normalize(projection(encoder(**batch).pooler_output), dim=-1)
        separator = ResUNet30(1, 1, 512)
        separator.load_state_dict({key.removeprefix("ss_model."): tensor for key, tensor in state.items()
                                   if key.startswith("ss_model.")})
        self.model = separator.eval()

    def unload(self) -> None:
        self.model = None
        self.conditions = None
        if self.used_cuda:
            torch.cuda.empty_cache()
            self.used_cuda = False

    def separate(self, audio: Audio, device: Device, progress: ProgressCallback) -> InferenceResult:
        start = perf_counter()
        actual = resolve_device(device)
        allocated = reserved = None
        if actual.type == "cuda":
            self.used_cuda = True
            torch.cuda.reset_peak_memory_stats(actual)
            stems = self._cuda(audio, actual, progress)
            allocated = torch.cuda.max_memory_allocated(actual)
            reserved = torch.cuda.max_memory_reserved(actual)
            if stems is None:
                if device == "cuda":
                    raise SeparationError("音效模型显存不足，请改用 CPU。")
                progress(Progress("device", "显存不足，改用 CPU。"))
                actual = torch.device("cpu")
                self.model.to(actual)
                torch.cuda.empty_cache()
                stems = self._infer(audio, actual, self._cpu_seconds(), progress)
        else:
            stems = self._infer(audio, actual, self._cpu_seconds(), progress)
        return InferenceResult(stems, device_label(actual), perf_counter() - start, allocated, reserved)

    def _cuda(self, audio: Audio, device: torch.device, progress: ProgressCallback) -> dict[str, Audio] | None:
        sizes = (2.5,) if self.quality == "fast" else (5, 2.5)
        for seconds in sizes:
            try:
                return self._infer(audio, device, seconds, progress)
            except torch.cuda.OutOfMemoryError:
                pass
            torch.cuda.empty_cache()
            progress(Progress("device", "音效模型显存不足，正在缩短分块。"))
        return None

    def _cpu_seconds(self) -> float:
        return 2.5 if self.quality == "fast" else 3

    @torch.inference_mode()
    def _infer(self, audio: Audio, device: torch.device, seconds: float,
               progress: ProgressCallback) -> dict[str, Audio]:
        if self.model is None or self.conditions is None:
            raise SeparationError("请先加载音效模型。")
        model = self.model.to(device)
        conditions = self.conditions.to(device)
        mixed_precision = device.type == "cuda" and torch.cuda.is_bf16_supported(including_emulation=False)
        remaining = torch.from_numpy(audio.copy())
        chunk = round(seconds * MODEL_RATE)
        context = MODEL_RATE // 2
        body = chunk - 2 * context
        half = min(context, body // 2, round(SEAM_FADE_SECONDS * MODEL_RATE / 2))
        fade_in = torch.linspace(0, 1, 2 * half, dtype=torch.float32)
        fade_in = .5 - .5 * torch.cos(torch.pi * fade_in)
        model_frames = (audio.shape[-1] * MODEL_RATE + EXPORT_RATE - 1) // EXPORT_RATE
        offsets = range(0, model_frames, body)
        # Treat the stereo channels as a batch of mono inputs, keeping the original frame grid.
        total = len(offsets) * len(self.targets)
        done = 0
        label = device_label(device)
        progress(Progress("inference", label, done, total, model=self.spec.name, device=label))
        output = {}
        for index, name in enumerate(self.targets):
            mix = resample(remaining, EXPORT_RATE, MODEL_RATE)
            padded = nn.functional.pad(mix, (context, context))
            extracted = torch.empty_like(mix)
            condition = conditions[index:index + 1].expand(audio.shape[0], -1)
            for offset in offsets:
                part = padded[:, offset:offset + chunk]
                part = nn.functional.pad(part, (0, chunk - part.shape[-1])).unsqueeze(1).to(device)
                with torch.autocast("cuda", dtype=torch.bfloat16) if mixed_precision else nullcontext():
                    estimate = model({"mixture": part, "condition": condition})["waveform"]
                stitch_effect_chunk(extracted, estimate[:, 0], offset, body, context, fade_in)
                done += 1
                progress(Progress("inference", label, done, total, model=self.spec.name, device=label))
            upsampled = resample(extracted, MODEL_RATE, EXPORT_RATE)[:, :audio.shape[-1]]
            output[name] = upsampled.numpy()
            remaining -= upsampled
        output[REMAINDER] = remaining.numpy()
        return output
