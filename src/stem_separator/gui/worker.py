"""Run a separation job in a child process and report events as JSON lines."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from typing import cast

from stem_separator.application.types import Device, MODEL_LABELS, ModelChoice, Progress, QUALITY_LABELS, Quality, SeparationRequest
from stem_separator.application.runtime_stats import peak_rss_bytes


class Cancelled(Exception):
    pass


def emit(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}), flush=True)


def main() -> int:
    os.environ.update(
        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1",
    )
    action, model_dir, quality_name, *paths = sys.argv[1:]
    if quality_name not in QUALITY_LABELS:
        return 2
    quality = cast(Quality, quality_name)
    root = Path(model_dir)
    cancel_path = os.environ.get("STEM_SEPARATOR_CANCEL_FILE")
    marker = Path(cancel_path) if cancel_path else None

    def check_cancel() -> None:
        if marker is not None and marker.is_file():
            raise Cancelled

    def progress(update: Progress) -> None:
        check_cancel()
        emit("progress", **asdict(update))

    try:
        check_cancel()
        if action == "recognize":
            from stem_separator.application.recognition import recognize

            start = perf_counter()
            result = recognize(Path(paths[0]), root, quality, progress, check_cancel, use_passt=quality == "high")
            check_cancel()
            emit("result", targets=result.tracks, uncertain=result.uncertain,
                 has_audio=result.has_audio, warning=result.warning, evidence=[asdict(item) for item in result.evidence],
                 elapsed_seconds=perf_counter() - start, peak_rss_bytes=peak_rss_bytes())
        elif action == "separate":
            from stem_separator.application.separation import SeparationService
            from stem_separator.backends.selection import build_backend
            source, output_root = map(Path, paths[:2])
            if paths[3] not in MODEL_LABELS:
                return 2
            model = cast(ModelChoice, paths[3])
            service = SeparationService(build_backend(root, tuple(paths[4:]), quality, model))
            device = cast(Device, os.environ.get("STEM_SEPARATOR_BENCH_DEVICE", "auto"))
            result = service.run(SeparationRequest(source, output_root, device), progress,
                                 filter_empty=paths[2] == "automatic",
                                 check_cancel=check_cancel)
            check_cancel()
            emit("result", tracks=list(result.paths), paths={name: str(path) for name, path in result.paths.items()},
                 device=result.device, frames=result.frames, sample_rate=result.sample_rate,
                 model=result.model, diagnostics=result.diagnostics, validation_notice=result.validation_notice,
                 export_gain=result.export_gain,
                 elapsed_seconds=result.elapsed_seconds, inference_seconds=result.inference_seconds,
                 peak_bytes=result.cuda_peak_allocated_bytes, reserved_bytes=result.cuda_peak_reserved_bytes,
                 peak_rss_bytes=peak_rss_bytes())
        else:
            return 2
    except Cancelled:
        return 130
    except Exception as error:
        emit("error", message=str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
