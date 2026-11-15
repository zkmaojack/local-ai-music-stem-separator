from collections.abc import Callable
from pathlib import Path
import subprocess

import imageio_ffmpeg
import numpy as np

from stem_separator.application.types import SeparationError
from stem_separator.backends.base import Audio


def has_signal(audio: Audio) -> bool:
    """Ignore numerical silence without requiring a loud peak or whole-song RMS."""
    return bool(audio.size and max(float(audio.max()), -float(audio.min())) > 2e-6)


def decode_audio(
    source: Path, sample_rate: int, channels: int, ffmpeg: str | None = None,
    *, check_cancel: Callable[[], None] | None = None,
) -> Audio:
    source = source.resolve()
    if not source.is_file():
        raise SeparationError(f"Input audio file was not found: {source}")

    try:
        executable = ffmpeg or imageio_ffmpeg.get_ffmpeg_exe()
        with subprocess.Popen(
            [
                executable,
                "-hide_banner", "-nostdin", "-loglevel", "error", "-xerror",
                "-protocol_whitelist", "file,pipe", "-i", str(source),
                "-map", "0:a:0", "-ac", str(channels), "-ar", str(sample_rate),
                "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as process:
            try:
                while True:
                    if check_cancel is not None:
                        check_cancel()
                    try:
                        stdout, stderr = process.communicate(timeout=.15 if check_cancel is not None else None)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except BaseException:
                process.kill()
                process.communicate()
                raise
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, process.args, stderr=stderr)
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode("utf-8", errors="replace").strip()
        raise SeparationError(f"FFmpeg could not decode {source.name}: {detail}") from error
    except (OSError, RuntimeError) as error:
        raise SeparationError(f"Could not start FFmpeg: {error}") from error

    bytes_per_frame = channels * np.dtype("<f4").itemsize
    if not stdout or len(stdout) % bytes_per_frame:
        raise SeparationError(f"FFmpeg returned empty or incomplete audio for {source.name}")
    audio = np.frombuffer(stdout, dtype="<f4").reshape(-1, channels).T.copy()
    if not np.isfinite(audio).all():
        raise SeparationError(f"Input contains non-finite audio samples: {source.name}")
    return audio
