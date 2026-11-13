"""Paint molded keycaps and their sockets in Qt logical pixels."""

from functools import lru_cache
from math import ceil

import numpy as np
from PySide6.QtCore import QEvent, QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QPushButton

from stem_separator.gui.theme import INK

# A single light above and to the left of the control surface.
LIGHT_X, LIGHT_Y = -0.48, -0.63


@lru_cache(maxsize=96)
def _surface(width: int, height: int, scale: float, radius: float, shoulder: float,
             tone: tuple[int, int, int], relief: float) -> QPixmap:
    """Shade the normals of a rounded solid, with a flat, finely textured face."""
    pw, ph = ceil(width * scale), ceil(height * scale)
    x = (np.arange(pw, dtype=np.float32)[None, :] + 0.5) / scale - width / 2
    y = (np.arange(ph, dtype=np.float32)[:, None] + 0.5) / scale - height / 2
    qx = np.abs(x) - (width / 2 - radius)
    qy = np.abs(y) - (height / 2 - radius)
    ox, oy = np.maximum(qx, 0), np.maximum(qy, 0)
    length = np.hypot(ox, oy)
    distance = length + np.minimum(np.maximum(qx, qy), 0) - radius
    nx = np.where(length > 0, ox / np.maximum(length, 1e-4), qx >= qy) * np.sign(x)
    ny = np.where(length > 0, oy / np.maximum(length, 1e-4), qy > qx) * np.sign(y)
    edge = np.clip(1 + distance / shoulder, 0, 1)
    slope = np.sin(edge * np.pi / 2)
    # A rounded shoulder gives a broad reflection. The lower lip falls into shade.
    directional = nx * LIGHT_X + ny * LIGHT_Y
    light = relief * (slope * directional - 0.24 * edge**3)
    grain = np.random.default_rng(9).normal(0, 0.85, (ph, pw))
    rgb = np.asarray(tone) + (light + grain)[..., None]
    pixels = np.empty((ph, pw, 4), dtype=np.uint8)
    pixels[:, :, :3] = np.rint(rgb).clip(0, 255)
    pixels[:, :, 3] = np.rint(np.clip(0.5 - distance * scale, 0, 1) * 255)
    image = QImage(pixels.data, pw, ph, pw * 4, QImage.Format.Format_RGBA8888).copy()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(scale)
    return pixmap


class HardwareButton(QPushButton):
    def __init__(self, text: str, *, width: int = 106, primary: bool = False,
                 glyph: str = "", compact: bool = False) -> None:
        super().__init__(text)
        self.primary = primary
        self.glyph = glyph
        self.compact = compact
        self.setMinimumSize(width, 36 if compact else 48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._travel = 0.0
        self._focus_visible = False
        self._motion = QVariantAnimation(self)
        self._motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._motion.valueChanged.connect(self._move_cap)
        self.pressed.connect(self._sync_travel)
        self.released.connect(self._sync_travel)
        self.toggled.connect(self._sync_travel)

    def _sync_travel(self) -> None:
        target = float(self.isDown() or self.isChecked())
        self._motion.stop()
        if target == self._travel:
            self.update()
            return
        self._motion.setDuration(95 if target else 135)
        self._motion.setStartValue(self._travel)
        self._motion.setEndValue(target)
        self._motion.start()

    def _move_cap(self, travel: float) -> None:
        self._travel = travel
        self.update()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._focus_visible = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                                                 Qt.FocusReason.ShortcutFocusReason)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        was_down = self.isDown()
        super().mouseReleaseEvent(event)
        if was_down and not self.isDown() and self._travel < 0.32:
            self._move_cap(0.32)
        self._sync_travel()

    def keyReleaseEvent(self, event) -> None:
        was_down = self.isDown()
        super().keyReleaseEvent(event)
        if was_down and not self.isDown():
            if self._travel < 0.32:
                self._move_cap(0.32)
            self._sync_travel()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange and hasattr(self, "_motion"):
            self._sync_travel()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        dpr = self.devicePixelRatioF()
        housing = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        cap_width = int(housing.width()) - (8 if self.compact else 10)
        cap_height = int(housing.height()) - (9 if self.compact else 12)
        if self.glyph:
            cap_width = cap_height = min(cap_width, cap_height)
        radius = cap_height / 2 if self.glyph else 7 if self.compact else 10
        cap = QRectF(0, 0, cap_width, cap_height)
        cap.moveCenter(housing.center() + QPointF(0, -1.7 + 2 * self._travel))

        p.setOpacity(1.0 if self.isEnabled() else 0.54)
        flange = _surface(int(housing.width()), int(housing.height()), dpr, 7, 2.5, (167, 166, 160), 30)
        p.drawPixmap(housing.topLeft(), flange)
        p.setBrush(QColor("#575751" if not self.compact else "#343736"))
        p.drawRoundedRect(housing.adjusted(1.5, 1.5, -1.5, -1.5), 5.5, 5.5)
        p.setBrush(QColor("#bcbab1" if not self.compact else "#626663"))
        p.drawRoundedRect(housing.adjusted(2.7, 2.4, -2.7, -3.0), 4.5, 4.5)

        clearance = 1 - self._travel
        for spread, alpha in ((2.1, 17), (1.25, 27), (0.55, 49)):
            contact = cap.adjusted(-spread, -0.3, spread, spread)
            contact.translate(0.25 + 0.65 * clearance, 0.65 + 1.7 * clearance)
            p.setBrush(QColor(28, 27, 23, round(alpha * (0.43 + 0.57 * clearance))))
            p.drawRoundedRect(contact, radius + spread / 2, radius + spread / 2)
        skirt = 0.65 + 1.9 * clearance
        p.setBrush(QColor("#8c8a80" if not self.isChecked() else "#474a46"))
        p.drawRoundedRect(cap.translated(0.35, skirt), radius, radius)

        hover = self.underMouse() and self.isEnabled()
        if self.isChecked():
            tone = (79, 84, 81)
        else:
            tone = (233, 231, 223) if self.primary else (219, 218, 211)
        face = _surface(cap_width, cap_height, dpr, radius, 3.5 if self.compact else 5.5,
                        tone, (23 if self.compact else 32) + 5 * hover - 8 * (clearance < 0.45))
        p.drawPixmap(cap.topLeft(), face)
        if self.hasFocus() and self._focus_visible:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#d9dfdb" if self.compact else "#323633"), 1, Qt.PenStyle.DashLine))
            p.drawRoundedRect(housing.adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)

        ink = QColor("#f1f1ec" if self.isChecked() else INK)
        if self.glyph:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
            center = cap.center()
            if self.glyph == "pause":
                for x in (-5.5, 1.75):
                    p.drawRoundedRect(QRectF(center + QPointF(x, -6.5), center + QPointF(x + 3.75, 6.5)), 0.65, 0.65)
            else:
                p.drawPolygon(QPolygonF([center + QPointF(-4, -7), center + QPointF(7, 0), center + QPointF(-4, 7)]))
        else:
            p.setPen(ink)
            font = self.font()
            font.setWeight(font.Weight.DemiBold if self.primary else font.Weight.Medium)
            p.setFont(font)
            p.drawText(cap, Qt.AlignmentFlag.AlignCenter, self.text())
