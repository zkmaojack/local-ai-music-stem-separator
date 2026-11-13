from hashlib import sha256
from pathlib import Path
import subprocess

import imageio_ffmpeg
import numpy as np
import pytest
import soundfile as sf

from stem_separator.application.types import SeparationError
from stem_separator.audio.decode import decode_audio
from stem_separator.backends.base import ModelSpec
from stem_separator.storage import model_cache
from stem_separator.storage.export import copy_stems, export_stems

SPEC = ModelSpec("htdemucs", 44100, 2, ("vocals", "drums", "bass", "other"))


@pytest.mark.parametrize("extension", ["wav", "flac", "mp3"])
def test_decode_resamples_and_duplicates_mono(tmp_path: Path, extension: str) -> None:
    original = tmp_path / "输入 file.wav"
    samples = 0.2 * np.sin(2 * np.pi * 440 * np.arange(48000) / 48000)
    sf.write(original, samples, 48000)
    source = original
    if extension != "wav":
        source = original.with_suffix(f".{extension}")
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-i", str(original), str(source)],
            check=True,
        )

    audio = decode_audio(source, 44100, 2)
    assert audio.shape == (2, 44100)
    assert audio.dtype == np.float32
    np.testing.assert_array_equal(audio[0], audio[1])
    assert np.abs(audio).max() > 0.1


def test_rejects_bad_audio(tmp_path: Path) -> None:
    source = tmp_path / "broken.flac"
    source.write_bytes(b"invalid FLAC")
    with pytest.raises(SeparationError, match="FFmpeg could not decode"):
        decode_audio(source, 44100, 2)


def test_export_preserves_alignment_and_relative_gain(tmp_path: Path) -> None:
    output = tmp_path / "song"
    stems = {name: np.zeros((2, 32), dtype=np.float32) for name in SPEC.stems}
    for index, samples in enumerate(stems.values(), 1):
        samples[:, 10] = index * 0.5

    paths, gain = export_stems(output, stems, SPEC, 32)
    assert paths.keys() == stems.keys()
    assert gain == pytest.approx(0.5, abs=1e-6)
    for name, path in paths.items():
        info = sf.info(path)
        assert (info.format, info.subtype, info.samplerate, info.channels, info.frames) == (
            "WAV", "PCM_24", 44100, 2, 32
        )
        actual, _ = sf.read(path, dtype="float32", always_2d=True)
        np.testing.assert_allclose(actual, stems[name].T * gain, atol=2**-23)

    with pytest.raises(SeparationError, match="already exists"):
        export_stems(output, stems, SPEC, 32)


def test_export_cleans_up_if_a_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_write = sf.write

    def fail_second_write(path: Path, *args, **kwargs) -> None:
        if path.name == "drums.wav":
            raise OSError("disk full")
        real_write(path, *args, **kwargs)

    monkeypatch.setattr(sf, "write", fail_second_write)
    stems = {name: np.zeros((2, 32), dtype=np.float32) for name in SPEC.stems}
    with pytest.raises(SeparationError, match="disk full"):
        export_stems(tmp_path / "song", stems, SPEC, 32)
    assert list(tmp_path.iterdir()) == []


def test_copy_exports_only_selected_tracks_and_preserves_bytes(tmp_path: Path) -> None:
    source = tmp_path / "preview"
    stems = {name: np.zeros((2, 32), dtype=np.float32) for name in SPEC.stems}
    paths, _ = export_stems(source, stems, SPEC, 32)
    destination = tmp_path / "saved"

    copy_stems(destination, [paths["vocals"], paths["bass"]])
    assert sorted(path.name for path in destination.iterdir()) == ["bass.wav", "vocals.wav"]
    assert (destination / "vocals.wav").read_bytes() == paths["vocals"].read_bytes()
    with pytest.raises(SeparationError, match="already exists"):
        copy_stems(destination, [paths["drums"]])


def test_cached_weights_are_verified_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_cache, "CHECKPOINT_SHA256", sha256(b"verified").hexdigest())
    cache = model_cache.ModelCache(tmp_path)

    def fail_network(*_args, **_kwargs):
        pytest.fail("Local checkpoint loading attempted to use the network")

    monkeypatch.setattr(model_cache, "urlopen", fail_network)
    with pytest.raises(SeparationError, match="missing"), cache.open_checkpoint():
        pass
    cache.path.write_bytes(b"damaged")
    with pytest.raises(SeparationError, match="SHA-256 mismatch"), cache.open_checkpoint():
        pass
    cache.path.write_bytes(b"verified")
    with cache.open_checkpoint() as file:
        assert file.read() == b"verified"
    assert cache.download() == cache.path
