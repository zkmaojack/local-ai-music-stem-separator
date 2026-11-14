from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog

from stem_separator.audio.playback import load_preview
from stem_separator.gui import app as gui
from stem_separator.gui.audio_cache import AudioCache
from stem_separator.gui.processing import Stage
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


def test_cancelling_finalization_releases_preview_and_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(gui, "gettempdir", lambda: str(tmp_path))
    app = QApplication.instance() or QApplication([])
    window = gui.MainWindow(tmp_path / "models")
    try:
        window.source = tmp_path / "track.wav"
        window.pages.setCurrentWidget(window.workspace)
        window.show()
        app.processEvents()
        window.session = AudioCache(window.cache_root)
        cache = window.session.path
        path = cache / "vocals.wav"
        sf.write(path, np.zeros((441, 2), dtype=np.float32), 44100)
        preview = load_preview([path], cache / ".preview")
        window.paths = {"vocals": path}
        window.phase = "preview"
        window._set_busy(True)
        window._show_stage(Stage.FINALIZING)
        assert window.signal_display.timer.isActive()

        window._cancel()
        assert not window.signal_display.timer.isActive()
        with pytest.raises(InterruptedError):
            window._check_preview_cancel()
        window._preview_loaded(preview, "")

        assert window.processing_stage == Stage.CANCELLED
        assert preview.samples[0]._mmap.closed
        assert not cache.exists()
        assert not window.paths
    finally:
        window._set_busy(False)
        window.close()


def test_replacement_failure_and_cancel_keep_the_committed_preview(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(gui, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(gui, "assets_for_targets", lambda *_: ())
    app = QApplication.instance() or QApplication([])
    window = gui.MainWindow(tmp_path / "models")
    window.show()
    app.processEvents()
    starts = []
    monkeypatch.setattr(window.job, "start", lambda *args: starts.append(args))
    try:
        window.source = tmp_path / "original.wav"
        window.session = AudioCache(window.cache_root)
        original = window.session
        stem = original.path / "vocals.wav"
        sf.write(stem, np.zeros((441, 2), dtype=np.float32), 44100)
        window.paths = {"vocals": stem}
        preview = load_preview([stem], original.path / ".preview")
        window.results.set_preview(preview, ("vocals",))
        window.results.seek(.5)
        window.results.mutes[0].click()
        player = window.results.player
        song = tmp_path / "replacement.wav"
        song.touch()

        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: ("", ""))
        window.choose_file()
        assert starts == [] and window.results.player is player and stem.exists()
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(song), ""))
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
        window.change.click()
        assert starts == [] and window.results.player is player and stem.exists()
        window._begin_song(gui.SongSetup(song, "fast", "demucs", ()))
        assert starts[-1][-2:] == ("manual", "demucs")
        assert window.session is not original and stem.exists()
        candidate = window.session.path
        window.job_error = "candidate model failed"
        window._on_finished(False)
        assert not candidate.exists() and window.session is original
        assert window.source.name == "original.wav" and window.results.player is player
        assert window.results.mutes[0].isChecked() and abs(player.position / preview.frames - .5) < .01

        window._begin_song(gui.SongSetup(song, "balanced", "subroformer", ()))
        next_cache = window.session.path
        window.phase = "preview"
        window._cancel()
        window._preview_loaded(None, "cancelled")
        assert window.session is original and not next_cache.exists()

        queued_song = tmp_path / "queued.wav"
        window._begin_song(gui.SongSetup(song, "fast", "demucs", ()))
        replaced_cache = window.session.path
        window.phase = "preview"
        window._begin_song(gui.SongSetup(queued_song, "balanced", "subroformer", ()))
        assert window.source == song and window.pending_setup.source == queued_song
        window._preview_loaded(None, "cancelled")
        assert not replaced_cache.exists() and window.source == queued_song and stem.exists()
        window._set_busy(False)
        window._restore_result()

        window._begin_song(gui.SongSetup(song, "balanced", "subroformer", ()))
        window.preview_cancel.clear()
        new_path = window.session.path / "vocals.wav"
        sf.write(new_path, np.zeros((601, 2), dtype=np.float32), 44100)
        window.paths = {"vocals": new_path}
        window._preview_loaded(load_preview([new_path], window.session.path / ".preview"), "")
        assert not original.path.exists() and window.results.player.preview.frames == 601
        assert window.source == song
    finally:
        window._set_busy(False)
        window.close()
