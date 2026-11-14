import logging
from time import perf_counter

from demucs.apply import apply_model
from demucs.htdemucs import HTDemucs
from demucs.states import load_model
import torch

from stem_separator.application.types import Device, Progress, ProgressCallback, SeparationError
from stem_separator.backends.base import Audio, InferenceResult, ModelSpec
from stem_separator.storage.model_cache import ModelCache

LOGGER = logging.getLogger(__name__)
OVERLAP = 0.25


class DemucsBackend:
    spec = ModelSpec("htdemucs", 44100, 2, ("vocals", "drums", "bass", "other"))

    def __init__(self, cache: ModelCache, *, compact: bool = False) -> None:
        self._cache = cache
        self._model: HTDemucs | None = None
        self._used_cuda = False
        self._compact = compact

    def load(self) -> None:
        with self._cache.open_checkpoint() as checkpoint:
            # The official checkpoint stores the model class and constructor arguments.
            package = torch.load(checkpoint, map_location="cpu", weights_only=False)
        model = load_model(package)
        if (
            not isinstance(model, HTDemucs)
            or set(model.sources) != set(self.spec.stems)
            or model.samplerate != self.spec.sample_rate
            or model.audio_channels != self.spec.channels
        ):
            raise SeparationError("The checkpoint does not match the htdemucs four-stem model.")
        if self._compact:
            model.segment = min(4.0, float(model.segment))
        self._model = model.eval()

    def unload(self) -> None:
        self._model = None
        if self._used_cuda:
            torch.cuda.empty_cache()
            self._used_cuda = False

    def separate(
        self, audio: Audio, device: Device, progress: ProgressCallback
    ) -> InferenceResult:
        model = self._model
        if model is None:
            raise SeparationError("Load the Demucs model before running inference.")

        started = perf_counter()
        actual_device = resolve_device(device)
        mix = torch.from_numpy(audio)
        reference = mix.mean(dim=0)
        mean = reference.mean()
        scale = reference.std(unbiased=False)
        # An out-of-phase stereo mix can cancel when averaged to mono.
        if scale < 1e-8:
            scale = mix.std(unbiased=False).clamp_min(1e-8)
        normalized = ((mix - mean) / scale).unsqueeze(0)

        allocated = reserved = None
        if actual_device.type == "cuda":
            self._used_cuda = True
            torch.cuda.reset_peak_memory_stats(actual_device)
            estimates = self._try_cuda(model, normalized, actual_device, progress)
            allocated = torch.cuda.max_memory_allocated(actual_device)
            reserved = torch.cuda.max_memory_reserved(actual_device)
            if estimates is None:
                if device == "cuda":
                    raise SeparationError("CUDA ran out of memory. Close other GPU apps or use --device cpu.")
                progress(Progress("device", "CUDA ran out of memory. Retrying on CPU."))
                actual_device = torch.device("cpu")
                model.to(actual_device)
                torch.cuda.empty_cache()
                estimates = self._infer(model, normalized, actual_device, progress)
        else:
            estimates = self._infer(model, normalized, actual_device, progress)

        estimates.mul_(scale).add_(mean)
        return InferenceResult(
            stems=dict(zip(model.sources, estimates.numpy(), strict=True)),
            device=device_label(actual_device),
            elapsed_seconds=perf_counter() - started,
            cuda_peak_allocated_bytes=allocated,
            cuda_peak_reserved_bytes=reserved,
        )

    def _try_cuda(
        self, model: HTDemucs, mix: torch.Tensor, device: torch.device, progress: ProgressCallback
    ) -> torch.Tensor | None:
        try:
            return self._infer(model, mix, device, progress)
        except torch.cuda.OutOfMemoryError:
            LOGGER.debug("CUDA out of memory with the trained segment length", exc_info=True)

        original_segment = model.segment
        retry_segment = min(4.0, float(original_segment) / 2)
        torch.cuda.empty_cache()
        progress(Progress("device", f"CUDA ran out of memory. Retrying with {retry_segment:.1f}s chunks."))
        try:
            # HTDemucs.forward pads to model.segment even when apply_model uses a shorter chunk.
            model.segment = retry_segment
            return self._infer(model, mix, device, progress)
        except torch.cuda.OutOfMemoryError:
            LOGGER.debug("CUDA out of memory with reduced segment length", exc_info=True)
            return None
        finally:
            model.segment = original_segment

    @staticmethod
    def _infer(
        model: HTDemucs, mix: torch.Tensor, device: torch.device, progress: ProgressCallback
    ) -> torch.Tensor:
        segment_frames = int(model.samplerate * model.segment)
        stride = int((1 - OVERLAP) * segment_frames)
        total = len(range(0, mix.shape[-1], stride))
        label = device_label(device)
        completed = 0

        def on_chunk(_module: HTDemucs, _args: tuple[torch.Tensor, ...], _output: torch.Tensor) -> None:
            nonlocal completed
            completed += 1
            progress(Progress("inference", label, completed, total))

        progress(Progress("inference", label, 0, total))
        hook = model.register_forward_hook(on_chunk)
        try:
            # A CPU input keeps the full-length output and overlap buffers in system RAM.
            return apply_model(model, mix, device=device, split=True, overlap=OVERLAP, shifts=0)[0]
        finally:
            hook.remove()


def resolve_device(requested: Device) -> torch.device:
    if requested == "cpu":
        return torch.device("cpu")
    try:
        return probe_cuda()
    except (RuntimeError, AssertionError) as error:
        if requested == "cuda":
            raise SeparationError(f"CUDA is unavailable: {error}") from error
        LOGGER.warning("CUDA unavailable, using CPU: %s", error)
        return torch.device("cpu")


def probe_cuda() -> torch.device:
    if not torch.cuda.is_available():
        raise RuntimeError("PyTorch did not detect a usable CUDA device.")
    device = torch.device("cuda:0")
    with torch.no_grad():
        probe = torch.ones((16, 16), device=device)
        if (probe @ probe)[0, 0].item() != 16:
            raise RuntimeError("The CUDA computation check returned an unexpected value.")
    return device


def device_label(device: torch.device) -> str:
    return f"CUDA ({torch.cuda.get_device_name(device)})" if device.type == "cuda" else "CPU"
