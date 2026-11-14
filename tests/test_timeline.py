import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from stem_separator.audio.playback import Preview, WaveformPeaks
from stem_separator.gui.controls import plot_x
from stem_separator.gui.results import Results


def test_scrubber_and_all_plots_share_coordinates_when_the_viewport_changes(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    results = Results()
    results.show()
    zeros = np.zeros((1, 2), dtype=np.float32)
    samples = np.zeros((4000, 2), dtype=np.float32)
    peaks = WaveformPeaks(zeros, zeros, zeros, len(samples), len(samples))
    tracks = ("vocal", "electric-guitar", "double-bass", "piano", "cello", "viola", "flute", "remainder")
    results.set_preview(Preview((samples,) * len(tracks), (peaks,) * len(tracks), 44100), tracks)
    try:
        for width, height, scrolls in ((1200, 780, False), (690, 375, True)):
            results.resize(width, height)
            app.processEvents()
            assert results.display.verticalScrollBar().isVisible() == scrolls
            for fraction in (0., .25, .5, .75, 1.):
                results.seek(fraction)
                slider = results.scrub
                thumb = slider.mapTo(results, QPoint()).x() + plot_x(slider.fraction, slider.width())
                for plot in (results.timeline, *results.waveforms):
                    assert plot.mapTo(results, QPoint()).x() + plot_x(plot.position, plot.width()) == thumb
                assert results.timeline.position == fraction
            target = round(plot_x(.25, results.timeline.width()))
            QTest.mouseClick(results.timeline, Qt.MouseButton.LeftButton, pos=QPoint(target, 10))
            assert results.timeline.position == pytest.approx(.25, abs=.002)
            drag_from, drag_to = (QPoint(round(plot_x(f, slider.width())), 15) for f in (.25, .75))
            QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=drag_from)
            QTest.mouseMove(slider, drag_to)
            assert results.timeline.position == pytest.approx(.75, abs=.002)
            assert results.player.position / results.player.preview.frames == pytest.approx(.25, abs=.002)
            QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=drag_to)
            assert results.player.position / results.player.preview.frames == pytest.approx(.75, abs=.002)
    finally:
        results.clear()
        results.close()


def test_hover_updates_timestamp_across_tracks_without_seeking(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    results = Results()
    results.resize(900, 500)
    results.show()
    samples = np.zeros((4000, 2), dtype=np.float32)
    peaks = WaveformPeaks(samples[:1], samples[:1], samples[:1], len(samples), len(samples))
    results.set_preview(Preview((samples,) * 2, (peaks,) * 2, 100), ("drums", "synth"))
    try:
        results.seek(.35)
        app.processEvents()
        results.solos[0].click()
        wave = results.waveforms[1]
        def move(point):
            position = QPointF(point)
            event = QMouseEvent(QEvent.Type.MouseMove, position, wave.mapToGlobal(position),
                                Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(wave, event)

        for fraction, timestamp in zip((0, .25, .5, .75, 1), ("0:00.0", "0:10.0", "0:20.0", "0:30.0", "0:40.0")):
            point = QPoint(round(plot_x(fraction, wave.width())), wave.height() // 2)
            move(point)
            assert results.hover.isVisible() and results.hover.timestamp == timestamp
            assert results.timeline.position == .35 and results.player.position == 1400
            assert results.hover._x == pytest.approx(
                results.timeline.mapTo(results.display.viewport(), QPoint()).x() + point.x(), abs=1)
        QTest.mouseClick(wave, Qt.MouseButton.LeftButton,
                         pos=QPoint(round(plot_x(.5, wave.width())), wave.height() // 2))
        assert results.player.position / len(samples) == pytest.approx(.5, abs=.002)
        results.resize(735, 410)
        app.processEvents()
        move(QPoint(round(plot_x(.5, wave.width())), wave.height() // 2))
        assert results.hover.timestamp == "0:20.0"
        QApplication.sendEvent(wave, QEvent(QEvent.Type.Leave))
        assert results.hover.isHidden()
    finally:
        results.clear()
        results.close()


def test_solo_overrides_mute_without_discarding_mix_or_locking_active_track(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    results = Results()
    samples = np.zeros((4000, 2), dtype=np.float32)
    peaks = WaveformPeaks(samples[:1], samples[:1], samples[:1], len(samples), len(samples))
    preview = Preview((samples,) * 3, (peaks,) * 3, 100)
    results.set_preview(preview, ("drums", "synth", "remainder"))
    try:
        results.volume.setValue(43)
        results.mutes[0].click()
        results.mutes[2].click()
        assert results.player.gains == (0., .43, 0.)
        results.solos[0].click()
        assert results.active_solo == "drums"
        assert results.player.gains == (.43, 0., 0.)
        assert results.mutes[0].isChecked()
        assert all(key.isEnabled() for key in (results.mutes[0], results.solos[0], results.play_key,
                                                results.scrub, results.volume))
        assert all(not key.isEnabled() for key in (*results.mutes[1:], *results.solos[1:]))
        results.solos[1].click()
        assert results.active_solo == "drums"
        results.seek(.5)
        assert results.player.position == len(samples) // 2
        results.solos[0].click()
        assert results.active_solo is None
        assert results.player.gains == (0., .43, 0.) and results.volume.value() == 43
        assert all(key.isEnabled() for key in (*results.mutes, *results.solos))
        results.solos[0].click()
        results.set_track_visible("drums", False)
        assert results.active_solo is None and results.player.gains == (0., .43, 0.)
        results.solos[1].click()
        results.set_preview(preview, ("strings", "flute", "remainder"))
        assert results.active_solo is None
        assert results.player.gains == (.43, .43, .43)
        app.processEvents()
    finally:
        results.clear()
        results.close()
