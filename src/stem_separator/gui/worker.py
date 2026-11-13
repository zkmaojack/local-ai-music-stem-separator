"""Run a separation job in a child process and report events as JSON lines."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from stem_separator.application.types import Progress, SeparationRequest
from stem_separator.storage.model_cache import assets_for_targets


class Cancelled(Exception):
    pass


def emit(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}), flush=True)


def main() -> int:
    action, model_dir, *paths = sys.argv[1:]
    targets = tuple(paths[2:]) if action == "separate" else tuple(paths)
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
        if action == "setup":
            for asset in assets_for_targets(root, targets):
                progress(Progress("download", asset.path.name))
                asset.download(lambda done, total: progress(Progress("download", asset.path.name, done, total)))
            emit("ready")
        else:
            from stem_separator.application.separation import SeparationService
            from stem_separator.backends.selection import build_backend
            source, output_root = map(Path, paths[:2])
            service = SeparationService(build_backend(root, targets))
            result = service.run(SeparationRequest(source, output_root), progress)
            check_cancel()
            emit("result", paths={name: str(path) for name, path in result.paths.items()},
                 device=result.device, frames=result.frames, sample_rate=result.sample_rate,
                 elapsed_seconds=result.elapsed_seconds, peak_bytes=result.cuda_peak_allocated_bytes)
    except Cancelled:
        return 130
    except Exception as error:
        emit("error", message=str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
