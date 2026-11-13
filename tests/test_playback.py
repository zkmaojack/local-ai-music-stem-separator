from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sd = pytest.importorskip("sounddevice")

from stem_separator.audio.playback import Preview, StemPlayer, load_preview


def test_preview_mixes_tracks_at_a_shared_cursor_after_seek() -> None:
    first = np.zeros((8, 2), dtype=np.float32)
    second = first.copy()
    first[4] = [0.8, 0.2]
    second[4] = [0.6, 0.4]
    player = StemPlayer(Preview((first, second), (), 44100))
    player.gains = (0.5, 0.25)
    player.seek(3)
    output = np.empty((3, 2), dtype=np.float32)
    time_info = SimpleNamespace(outputBufferDacTime=0.0)

    player._mix(output, len(output), time_info, None)
    np.testing.assert_allclose(output, [[0, 0], [0.55, 0.2], [0, 0]], atol=1e-6)
    assert player.position == 6

    player.seek(4)
    player.gains = (0, 1)
    player._mix(output, len(output), time_info, None)
    np.testing.assert_allclose(output, [[0.6, 0.4], [0, 0], [0, 0]], atol=1e-6)

    with pytest.raises(sd.CallbackStop):
        player._mix(output, len(output), time_info, None)
    np.testing.assert_array_equal(output, 0)
    assert player.position == 8


def test_preview_maps_aligned_audio_and_releases_files(tmp_path: Path) -> None:
    track = tmp_path / "track.wav"
    samples = np.zeros((5003, 2), dtype=np.float32)
    samples[5002, 1] = 0.75
    sf.write(track, samples, 44100, subtype="PCM_24")
    cache = tmp_path / "preview"
    preview = load_preview([track], cache)
    try:
        assert isinstance(preview.samples[0], np.memmap)
        assert preview.frames == len(samples)
        np.testing.assert_array_equal(preview.samples[0], samples)
        assert preview.waveforms[0][-1] == pytest.approx(0.75)
    finally:
        preview.close()
    (cache / "0.f32").unlink()
