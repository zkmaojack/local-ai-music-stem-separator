import argparse
import logging
from pathlib import Path
import subprocess
from typing import cast

from stem_separator.application.instruments import INSTRUMENTS
from stem_separator.application.types import Device, Progress, SeparationError
from stem_separator.storage.model_cache import ModelCache, effect_assets, model_assets

LOGGER = logging.getLogger("stem_separator")
MIB = 1024**2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Separate music locally into four WAV stems.")
    parser.add_argument("--model-dir", type=Path, default=Path(".model-cache"), help="Model cache directory")
    parser.add_argument("--verbose", action="store_true", help="Show debug logs and tracebacks")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup-model", help="Download and verify the selected model")
    setup.add_argument("--instruments", action="store_true", help="Set up the extended instrument model")
    setup.add_argument("--effects", action="store_true", help="Set up the electronic effects model")
    commands.add_parser("list-instruments", help="List instrument IDs for --instrument")
    commands.add_parser("doctor", help="Check FFmpeg, CUDA computation, and the local checkpoint")
    separate = commands.add_parser("separate", help="Run separation using only local files")
    separate.add_argument("source", type=Path, help="Path to an audio file")
    separate.add_argument("--output", type=Path, default=Path("output"), help="Root directory for exports")
    separate.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    separate.add_argument("--ffmpeg", help="Path to a local FFmpeg executable")
    separate.add_argument("--instrument", action="append", default=[], choices=[item.id for item in INSTRUMENTS],
                          help="Extract this instrument (repeat for multiple instruments)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    cache = ModelCache(args.model_dir)
    try:
        if args.command == "setup-model":
            assets = model_assets(args.model_dir, args.instruments) if args.instruments or not args.effects else ()
            for asset in assets + (effect_assets(args.model_dir) if args.effects else ()):
                LOGGER.info("Verified model: %s", asset.download())
        elif args.command == "list-instruments":
            for item in INSTRUMENTS:
                print(item.id, item.label, item.category, sep="\t")
        elif args.command == "doctor":
            return doctor(cache)
        else:
            run_separation(args, cache)
    except KeyboardInterrupt:
        LOGGER.warning("Cancelled.")
        return 130
    except (SeparationError, OSError, RuntimeError, ValueError, ImportError, subprocess.SubprocessError) as error:
        LOGGER.error("%s", error, exc_info=args.verbose)
        return 1
    return 0


def doctor(cache: ModelCache) -> int:
    import imageio_ffmpeg
    import torch

    from stem_separator.backends.demucs import DemucsBackend, device_label, probe_cuda

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    version = subprocess.check_output([ffmpeg, "-version"], text=True, encoding="utf-8", errors="replace")
    LOGGER.info("%s", version.splitlines()[0])
    LOGGER.info("PyTorch %s, CUDA runtime %s", torch.__version__, torch.version.cuda or "none")
    try:
        device = probe_cuda()
    except (RuntimeError, AssertionError) as error:
        LOGGER.warning("CUDA probe failed: %s. CPU inference is available.", error)
    else:
        properties = torch.cuda.get_device_properties(device)
        LOGGER.info("%s passed a matrix multiplication (sm_%d%d, %.0f MiB)",
                    device_label(device), properties.major, properties.minor, properties.total_memory / MIB)

    backend = DemucsBackend(cache)
    backend.load()
    LOGGER.info("Loaded %s from %s", backend.spec.name, cache.path)
    backend.unload()
    return 0


def run_separation(args: argparse.Namespace, cache: ModelCache) -> None:
    from stem_separator.application.separation import SeparationService
    from stem_separator.application.types import SeparationRequest
    from stem_separator.backends.selection import build_backend

    request = SeparationRequest(args.source, args.output, cast(Device, args.device))
    backend = build_backend(cache.path.parent, tuple(args.instrument))
    service = SeparationService(backend, args.ffmpeg)
    result = service.run(request, log_progress)
    LOGGER.info("%s on %s: %.2fs total, %.2fs inference; %.3fs audio at %d Hz (%d frames)",
                result.model, result.device, result.elapsed_seconds, result.inference_seconds,
                result.frames / result.sample_rate, result.sample_rate, result.frames)
    if result.cuda_peak_allocated_bytes is not None and result.cuda_peak_reserved_bytes is not None:
        LOGGER.info("PyTorch CUDA peak: %.0f MiB allocated, %.0f MiB reserved",
                    result.cuda_peak_allocated_bytes / MIB, result.cuda_peak_reserved_bytes / MIB)
    if result.export_gain < 1.0:
        LOGGER.info("Applied shared gain %.6f to all stems to avoid clipping", result.export_gain)
    for path in result.paths.values():
        print(path)


def log_progress(event: Progress) -> None:
    if event.total is None:
        LOGGER.info("%s", event.message)
    else:
        LOGGER.info("%s: %d/%d chunks (%.0f%%)", event.message, event.completed, event.total,
                    100 * (event.completed or 0) / event.total)


if __name__ == "__main__":
    raise SystemExit(main())
