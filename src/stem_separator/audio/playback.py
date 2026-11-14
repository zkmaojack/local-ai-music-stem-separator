"""Sample-aligned preview of separated WAV files through one audio stream."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
import sounddevice as sd
import soundfile as sf

from stem_separator.application.types import SeparationError

BLOCK_SIZE = 1024
PREVIEW_BINS = 3200
GAIN_RAMP_SECONDS = .008
# Give the output callback headroom while Qt repaints several tracks at once.
PLAYBACK_LATENCY = .125


@dataclass(frozen=True)
class WaveformPeaks:
    """Signed stereo extrema and energy for consecutive source frame ranges."""

    low: NDArray[np.float32]
    high: NDArray[np.float32]
    rms: NDArray[np.float32]
    frames: int
    stride: int

    def columns(self, width: int) -> tuple[NDArray, NDArray, NDArray, NDArray]:
        count = len(self.low)
        if width >= count:
            x = np.minimum((np.arange(count) + .5) * self.stride / self.frames, 1.)
            x[-1] = ((count - 1) * self.stride + self.frames) / (2 * self.frames)
            return x, self.low.min(axis=1), self.high.max(axis=1), self.rms.max(axis=1)
        starts = np.arange(width) * count // width
        stops = np.r_[starts[1:], count]
        low = np.minimum.reduceat(self.low.min(axis=1), starts)
        high = np.maximum.reduceat(self.high.max(axis=1), starts)
        energy = np.square(self.rms).mean(axis=1)
        weights = np.full(count, self.stride)
        weights[-1] = self.frames - (count - 1) * self.stride
        rms = np.sqrt(np.add.reduceat(energy * weights, starts) / np.add.reduceat(weights, starts))
        x = (starts * self.stride + np.minimum(stops * self.stride, self.frames)) / (2 * self.frames)
        return x, low, high, rms


@dataclass(frozen=True)
class Preview:
    samples: tuple[NDArray[np.float32], ...]
    waveforms: tuple[WaveformPeaks, ...]
    sample_rate: int

    @property
    def frames(self) -> int:
        return len(self.samples[0])

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate

    def close(self) -> None:
        for samples in self.samples:
            if isinstance(samples, np.memmap):
                samples._mmap.close()


def load_preview(paths: list[Path], cache: Path, *, check_cancel: Callable[[], None] | None = None) -> Preview:
    if not paths:
        raise SeparationError("没有可试听的音轨。")
    tracks: list[np.memmap] = []
    waveforms = []
    expected = None
    try:
        cache.mkdir(parents=True, exist_ok=True)
        for index, path in enumerate(paths):
            with sf.SoundFile(path) as file:
                shape = (file.frames, file.samplerate)
                if file.channels != 2 or not file.frames or (expected is not None and shape != expected):
                    raise SeparationError("试听音轨的采样率或长度不一致。")
                expected = shape
                stride = (file.frames + PREVIEW_BINS - 1) // PREVIEW_BINS
                lows, highs, powers = [], [], []
                target = cache / f"{index}.f32"
                with target.open("wb") as stream:
                    for block in file.blocks(blocksize=stride * 32, dtype="float32", always_2d=True):
                        if check_cancel is not None:
                            check_cancel()
                        block.tofile(stream)
                        starts = np.arange(0, len(block), stride)
                        lows.append(np.minimum.reduceat(block, starts, axis=0))
                        highs.append(np.maximum.reduceat(block, starts, axis=0))
                        counts = np.diff(np.r_[starts, len(block)])[:, None]
                        energy = np.add.reduceat(np.square(block), starts, axis=0)
                        powers.append(np.sqrt(energy / counts).astype(np.float32))
                tracks.append(np.memmap(target, dtype=np.float32, mode="r", shape=(file.frames, 2)))
                waveforms.append(WaveformPeaks(np.concatenate(lows), np.concatenate(highs),
                                               np.concatenate(powers), file.frames, stride))
    except BaseException:
        for samples in tracks:
            samples._mmap.close()
        raise
    return Preview(tuple(tracks), tuple(waveforms), expected[1])


class StemPlayer:
    def __init__(self, preview: Preview) -> None:
        self.preview = preview
        self._stream: sd.OutputStream | None = None
        self._cursor = 0
        self._dac_anchor: tuple[int, float] | None = None
        self._playing = False
        self._scratch = np.empty((BLOCK_SIZE, 2), dtype=np.float32)
        self.gains = (1.0,) * len(preview.samples)
        self._ramp_frames = max(1, round(preview.sample_rate * GAIN_RAMP_SECONDS))
        self._ramp_position = np.arange(1, BLOCK_SIZE + 1, dtype=np.float32)
        self._ramp_buffer = np.empty(BLOCK_SIZE, dtype=np.float32)
        self._last_gains = list(self.gains)
        self._target_gains = self.gains
        self._gain_steps = [0.] * len(self.gains)
        self._gain_remaining = [0] * len(self.gains)

    @property
    def playing(self) -> bool:
        return self._playing and self._stream is not None and self._stream.active

    @property
    def position(self) -> int:
        stream, anchor = self._stream, self._dac_anchor
        if stream is None or not self._playing or anchor is None:
            return self._cursor
        if not stream.active:
            return self._cursor
        frame, time = anchor
        elapsed = max(0.0, stream.time - time)
        return min(self._cursor, frame + int(elapsed * self.preview.sample_rate))

    def play(self) -> None:
        if self.playing:
            return
        if self._cursor == self.preview.frames:
            self._cursor = 0
        if self._stream is None:
            self._stream = sd.OutputStream(
                samplerate=self.preview.sample_rate, channels=2, dtype="float32",
                blocksize=BLOCK_SIZE, latency=PLAYBACK_LATENCY, callback=self._mix,
            )
        self._dac_anchor = None
        self._last_gains[:] = self.gains
        self._target_gains = self.gains
        self._gain_remaining[:] = [0] * len(self.gains)
        self._playing = True
        try:
            self._stream.start()
        except sd.PortAudioError:
            self.close()
            raise

    def pause(self) -> None:
        position = self.position
        if self.playing and self._stream is not None:
            self._stream.abort()
        self._playing = False
        self._cursor = position

    def seek(self, frame: int) -> None:
        resume = self.playing
        self.pause()
        self._cursor = max(0, min(frame, self.preview.frames))
        if resume and self._cursor < self.preview.frames:
            self.play()

    def close(self) -> None:
        stream, self._stream = self._stream, None
        self._playing = False
        if stream is not None:
            stream.close()

    def _mix(self, outdata, frames: int, time_info, _status) -> None:
        start = self._cursor
        count = min(frames, self.preview.frames - start)
        if self._dac_anchor is None:
            self._dac_anchor = (start, time_info.outputBufferDacTime)
        outdata.fill(0)
        if count:
            output = outdata[:count]
            scratch = self._scratch[:count]
            gains = self.gains
            for index, (samples, gain) in enumerate(zip(self.preview.samples, gains, strict=True)):
                if self._playing and gain != self._target_gains[index]:
                    self._gain_steps[index] = (gain - self._last_gains[index]) / self._ramp_frames
                    self._gain_remaining[index] = self._ramp_frames
                ramp = min(count, self._gain_remaining[index])
                block = samples[start:start + count]
                if ramp:
                    levels = self._ramp_buffer[:ramp]
                    step = self._gain_steps[index]
                    np.multiply(self._ramp_position[:ramp], step, out=levels)
                    levels += self._last_gains[index]
                    np.multiply(block[:ramp], levels[:, None], out=scratch[:ramp])
                    output[:ramp] += scratch[:ramp]
                    self._gain_remaining[index] -= ramp
                    self._last_gains[index] += step * ramp
                if gain and ramp < count:
                    np.multiply(block[ramp:], gain, out=scratch[ramp:])
                    output[ramp:] += scratch[ramp:]
                if not self._gain_remaining[index]:
                    self._last_gains[index] = gain
            self._target_gains = gains
            np.clip(output, -1.0, 1.0, out=output)
            self._cursor = start + count
        if self._cursor == self.preview.frames:
            raise sd.CallbackStop
