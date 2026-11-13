from pathlib import Path
import subprocess

import imageio_ffmpeg
import numpy as np

from stem_separator.application.types import SeparationError
from stem_separator.backends.base import Audio


def decode_audio(
    source: Path, sample_rate: int, channels: int, ffmpeg: str | None = None
) -> Audio:
    source = source.resolve()
    if not source.is_file():
        raise SeparationError(f"Input audio file was not found: {source}")

    try:
        executable = ffmpeg or imageio_ffmpeg.get_ffmpeg_exe()
        decoded = subprocess.run(
            [
                executable,
                "-hide_banner", "-nostdin", "-loglevel", "error", "-xerror",
                "-protocol_whitelist", "file,pipe", "-i", str(source),
                "-map", "0:a:0", "-ac", str(channels), "-ar", str(sample_rate),
                "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1",
            ],
            capture_output=True,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode("utf-8", errors="replace").strip()
        raise SeparationError(f"FFmpeg could not decode {source.name}: {detail}") from error
    except (OSError, RuntimeError) as error:
        raise SeparationError(f"Could not start FFmpeg: {error}") from error

    bytes_per_frame = channels * np.dtype("<f4").itemsize
    if not decoded.stdout or len(decoded.stdout) % bytes_per_frame:
        raise SeparationError(f"FFmpeg returned empty or incomplete audio for {source.name}")
    audio = np.frombuffer(decoded.stdout, dtype="<f4").reshape(-1, channels).T.copy()
    if not np.isfinite(audio).all():
        raise SeparationError(f"Input contains non-finite audio samples: {source.name}")
    return audio
