from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from stem_separator.audio.playback import load_preview
from stem_separator.gui import app as gui
from stem_separator.gui.audio_cache import AudioCache
from stem_separator.storage.export import copy_stems


@pytest.mark.parametrize("save", [False, True])
def test_closing_window_removes_temporary_audio(tmp_path: Path, monkeypatch, save: bool) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(gui, "gettempdir", lambda: str(tmp_path))
    app = QApplication.instance() or QApplication([])
    window = gui.MainWindow(tmp_path / "models")
    window.show()
    app.processEvents()

    window.session = AudioCache(window.cache_root)
    session = window.session.path
    track = session / "vocals.wav"
    sf.write(track, np.zeros((441, 2), dtype=np.float32), 44100)
    window.paths = {"vocals": track}
    preview = load_preview([track], session / ".preview")
    window.results.set_preview(preview, ("vocals",))
    exported = tmp_path / "export" / track.name
    if save:
        copy_stems(exported.parent, [track])

    assert window.close()
    assert preview.samples[0]._mmap.closed
    assert not session.exists()
    assert not list(window.cache_root.iterdir())
    assert exported.exists() == save


def test_recovers_only_abandoned_audio(tmp_path: Path) -> None:
    abandoned = tmp_path / ("job-" + "a" * 32)
    abandoned.mkdir()
    (abandoned / "vocals.wav").write_bytes(b"abandoned")
    unrelated = tmp_path / "my-audio"
    unrelated.mkdir()
    active = AudioCache(tmp_path)
    try:
        AudioCache.recover(tmp_path)

        assert not abandoned.exists()
        assert active.path.is_dir()
        assert unrelated.is_dir()
    finally:
        active.close()
