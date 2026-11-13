import os

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from stem_separator.gui.hardware import HardwareButton


def test_hardware_key_resets_after_release_outside_and_toggles_by_keyboard(monkeypatch) -> None:
    if not os.environ.get("QT_QPA_PLATFORM"):
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    key = HardwareButton("Solo", compact=True)
    key.setCheckable(True)
    key.show()
    app.processEvents()

    QTest.mousePress(key, Qt.MouseButton.LeftButton, pos=key.rect().center())
    QTest.qWait(140)
    assert key._travel == 1
    assert not key.grab().isNull()
    QTest.mouseRelease(key, Qt.MouseButton.LeftButton, pos=QPoint(-5, -5))
    QTest.qWait(190)
    assert key._travel == 0
    assert not key.isChecked()

    QTest.keyClick(key, Qt.Key.Key_Space)
    QTest.qWait(140)
    assert key.isChecked() and key._travel == 1
    QTest.keyClick(key, Qt.Key.Key_Space)
    QTest.qWait(190)
    assert not key.isChecked() and key._travel == 0
    key.close()
