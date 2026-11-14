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


def test_live_gain_switch_ramps_across_short_audio_callbacks() -> None:
    source = np.ones((40, 2), dtype=np.float32)
    player = StemPlayer(Preview((source,), (), 1000))
    player._playing = True
    player.gains = (0.,)
    out = np.empty((3, 2), dtype=np.float32)
    time_info = SimpleNamespace(outputBufferDacTime=0.)
    for expected in ((.875, .75, .625), (.5, .375, .25), (.125, 0., 0.), (0., 0., 0.)):
        player._mix(out, len(out), time_info, None)
        np.testing.assert_allclose(out, np.repeat(np.array(expected)[:, None], 2, axis=1))
    player.gains = (.8,)
    player._mix(out, len(out), time_info, None)
    np.testing.assert_allclose(out[:, 0], (.1, .2, .3), atol=1e-6)
    np.testing.assert_array_equal(source, 1.)


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
        shape = preview.waveforms[0]
        assert shape.high[-1, 1] == pytest.approx(0.75)
        assert shape.high[:, 0].max() == 0
        assert shape.rms[-1, 1] == pytest.approx(0.75)
        x, low, high, rms = shape.columns(12)
        assert 0 < x[0] < x[-1] < 1
        assert low.min() == 0 and high[-1] == pytest.approx(.75)
        assert rms[-1] == pytest.approx(.75 / np.sqrt(2 * (shape.frames - (len(shape.low) * 11 // 12) * shape.stride)))
    finally:
        preview.close()
    (cache / "0.f32").unlink()
