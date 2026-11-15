from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QObject, QPoint, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from stem_separator.gui.app import MainWindow
from stem_separator.gui.processing import Stage
from stem_separator.storage.export import copy_stems


def test_progress_is_stage_local_and_device_is_reported_by_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.source = Path("a very long filename " * 8 + ".flac")
    window.pages.setCurrentWidget(window.workspace)
    window._status("准备分离", "", cancellable=True)
    window.show()
    try:
        def report(stage, **fields):
            window._on_event(dict(event="progress", stage=stage, message="正在处理", **fields))

        report("inference", completed=3, total=4, model="Demucs", device="CUDA (Test GPU)")
        assert window.progress.fraction == .75
        assert window.processing_panel.percentage.text() == "75%"
        assert "CUDA" in window.processing_panel.device.text()
        report("load", model="AudioSep")
        assert window.progress.fraction is None
        assert window.processing_panel.percentage.text() == "进度待定"
        assert "等待后台确认" in window.processing_panel.device.text()
        report("inference", completed=0, total=20, model="AudioSep", device="CPU")
        assert window.progress.fraction == 0
        assert window.processing_panel.device.text() == "设备 · CPU"
        report("device")
        assert window.progress.fraction is None
        report("postprocess", model="AudioSep", device="CPU")
        assert window.processing_stage == Stage.FINALIZING
        report("export")
        assert window.progress.fraction is None

        for width, height in ((720, 540), (1200, 780), (1920, 1080)):
            window.resize(width, height)
            app.processEvents()
            panel = window.processing_panel
            body_center = panel.body.mapTo(panel, panel.body.rect().center())
            assert abs(body_center.x() - panel.rect().center().x()) <= 2
            assert abs(body_center.y() - panel.rect().center().y()) <= 2
            assert panel.rect().contains(panel.cancel.mapTo(panel, QPoint(0, panel.cancel.height())))
            assert panel.progress.height() == 14
            assert panel.filename.fontMetrics().horizontalAdvance(panel.filename.text()) <= panel.filename.width()
    finally:
        window.close()


def test_static_processing_has_no_timer_or_continuous_repaint(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.pages.setCurrentWidget(window.workspace)
    window._show_stage(Stage.LOADING)
    window.show()

    class Counter(QObject):
        paints = 0

        def eventFilter(self, widget, event):
            if event.type() == QEvent.Type.Paint:
                self.paints += 1
            return False

    counter = Counter()
    window.processing_panel.installEventFilter(counter)
    try:
        QTest.qWait(200)
        assert not window.processing_panel.findChildren(QTimer)
        counter.paints = 0
        QTest.qWait(400)
        assert counter.paints == 0
    finally:
        window.close()


def test_export_reports_real_bytes_and_cancellation_leaves_no_partial_folder(tmp_path):
    source = tmp_path / "track.wav"
    source.write_bytes(b"audio" * 1_000_000)
    events = []
    destination = tmp_path / "saved"
    copy_stems(destination, [source], progress=lambda done, total: events.append((done, total)))
    assert events[-1] == (source.stat().st_size, source.stat().st_size)
    assert (destination / source.name).read_bytes() == source.read_bytes()

    def cancel():
        if any(done > 0 for done, _ in events):
            raise InterruptedError("cancelled")

    events.clear()
    with pytest.raises(InterruptedError):
        copy_stems(tmp_path / "cancelled", [source], check_cancel=cancel,
                   progress=lambda done, total: events.append((done, total)))
    assert source.exists()
    assert not (tmp_path / "cancelled").exists()
    assert not list(tmp_path.glob(".stems-*"))
