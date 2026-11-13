"""Sample-aligned preview of separated WAV files through one audio stream."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
import sounddevice as sd
import soundfile as sf

from stem_separator.application.types import SeparationError

BLOCK_SIZE = 1024


@dataclass(frozen=True)
class Preview:
    samples: tuple[NDArray[np.float32], ...]
    waveforms: tuple[NDArray[np.float32], ...]
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


def load_preview(paths: list[Path], cache: Path) -> Preview:
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
                stride = (file.frames + 1199) // 1200
                peaks = []
                target = cache / f"{index}.f32"
                with target.open("wb") as stream:
                    for block in file.blocks(blocksize=stride * 32, dtype="float32", always_2d=True):
                        block.tofile(stream)
                        amplitude = np.max(np.abs(block), axis=1)
                        peaks.extend(np.maximum.reduceat(amplitude, np.arange(0, len(block), stride)))
                tracks.append(np.memmap(target, dtype=np.float32, mode="r", shape=(file.frames, 2)))
                waveforms.append(np.asarray(peaks, dtype=np.float32))
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
                blocksize=BLOCK_SIZE, latency="low", callback=self._mix,
            )
        self._dac_anchor = None
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
            for samples, gain in zip(self.preview.samples, self.gains, strict=True):
                if gain:
                    np.multiply(samples[start:start + count], gain, out=scratch)
                    np.add(output, scratch, out=output)
            np.clip(output, -1.0, 1.0, out=output)
            self._cursor = start + count
        if self._cursor == self.preview.frames:
            raise sd.CallbackStop
