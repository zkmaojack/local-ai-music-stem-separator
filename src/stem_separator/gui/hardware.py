"""Paint tactile keycaps in Qt logical pixels."""

from functools import lru_cache
from math import ceil

import numpy as np
from PySide6.QtCore import QEvent, QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QPushButton, QSizePolicy, QStyle

from stem_separator.gui.theme import (
    BUTTON_FACE, CONTACT_SHADOW, INK, KEY_CAP, KEY_DISABLED, KEY_DISABLED_INK,
    KEY_PRESSED, KEY_PRIMARY, KEY_SKIRT, KEY_TRAVEL, LIGHT_X, LIGHT_Y, RADIUS, SELECTION,
    SOLO_INK, TOOLBAR_KEY_HEIGHT, TRACK_KEY_SIZE,
)


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
    slope = edge**1.6
    directional = nx * LIGHT_X + ny * LIGHT_Y
    light = relief * slope * directional - 3 * y / height
    grain = np.random.default_rng(9).normal(0, 1.05, (ph, pw))
    rgb = np.asarray(tone) + (light + grain)[..., None]
    pixels = np.empty((ph, pw, 4), dtype=np.uint8)
    pixels[:, :, :3] = np.rint(rgb).clip(0, 255)
    pixels[:, :, 3] = np.rint(np.clip(0.5 - distance * scale, 0, 1) * 255)
    image = QImage(pixels.data, pw, ph, pw * 4, QImage.Format.Format_RGBA8888).copy()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(scale)
    return pixmap


class HardwareButton(QPushButton):
    def __init__(self, text: str, *, width: int | None = None, primary: bool = False,
                 glyph: str = "", compact: bool = False, dropdown: bool = False) -> None:
        super().__init__(text)
        self.primary = primary
        self.glyph = glyph
        self.compact = compact
        self.dropdown = dropdown
        self.setMinimumSize(width or (TRACK_KEY_SIZE if compact else 80),
                            TRACK_KEY_SIZE if compact else TOOLBAR_KEY_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._travel = 0.0
        self._mute_amount = 0.
        self.monitor_muted: bool | None = None
        self._solo_color = 0.
        self._focus_visible = False
        self._motion = QVariantAnimation(self)
        self._motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._motion.valueChanged.connect(self._move_cap)
        self.pressed.connect(self._sync_travel)
        self.released.connect(self._sync_travel)
        self.toggled.connect(self._sync_travel)
        if glyph == "speaker":
            self._speaker_motion = QVariantAnimation(self, duration=155)
            self._speaker_motion.setEasingCurve(QEasingCurve.Type.InOutCubic)
            self._speaker_motion.valueChanged.connect(self._move_speaker)
            self.toggled.connect(lambda _: self._sync_speaker())
        if glyph == "headphones":
            self._solo_motion = QVariantAnimation(self, duration=135)
            self._solo_motion.valueChanged.connect(self._move_solo_color)
            self.toggled.connect(self._sync_solo_color)

    def minimumSizeHint(self):
        return self.sizeHint()

    def sizeHint(self):
        size = super().sizeHint()
        padding = 42 if self.dropdown else 24
        size.setWidth(max(self.minimumWidth(), self.fontMetrics().horizontalAdvance(self.text()) + padding))
        size.setHeight(self.minimumHeight())
        return size

    def _animations_enabled(self) -> bool:
        return bool(self.style().styleHint(QStyle.StyleHint.SH_Widget_Animate))

    def set_monitor_muted(self, muted: bool | None) -> None:
        if muted != self.monitor_muted:
            self.monitor_muted = muted
            self._sync_speaker()

    def _sync_speaker(self) -> None:
        self._speaker_motion.stop()
        checked = self.isChecked() if self.monitor_muted is None else self.monitor_muted
        if not self._animations_enabled():
            self._move_speaker(float(checked))
        else:
            self._speaker_motion.setStartValue(self._mute_amount)
            self._speaker_motion.setEndValue(float(checked))
            self._speaker_motion.start()

    def _move_speaker(self, amount: float) -> None:
        self._mute_amount = amount
        self.update()

    def _sync_solo_color(self, checked: bool) -> None:
        self._solo_motion.stop()
        if not self._animations_enabled():
            self._move_solo_color(float(checked))
        else:
            self._solo_motion.setStartValue(self._solo_color)
            self._solo_motion.setEndValue(float(checked))
            self._solo_motion.start()

    def _move_solo_color(self, amount: float) -> None:
        self._solo_color = amount
        self.update()

    def _sync_travel(self) -> None:
        target = float(self.isDown() or (self.isChecked() and self.glyph not in ("speaker", "headphones")))
        self._motion.stop()
        if not self._animations_enabled():
            self._move_cap(target)
            return
        if target == self._travel:
            self.update()
            return
        self._motion.setDuration(100 if target else 135)
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
        if was_down and not self.isDown() and self._animations_enabled() and self._travel < 0.32:
            self._move_cap(0.32)
        self._sync_travel()

    def keyReleaseEvent(self, event) -> None:
        was_down = self.isDown()
        super().keyReleaseEvent(event)
        if was_down and not self.isDown():
            if self._animations_enabled() and self._travel < 0.32:
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
        checked_face = self.isChecked() and self.glyph != "headphones" and self.monitor_muted is None
        dpr = self.devicePixelRatioF()
        cap_width, cap_height = self.width() - 6, self.height() - 8
        radius = RADIUS if not self.compact else RADIUS - 1
        cap = QRectF(0, 0, cap_width, cap_height)
        cap.moveTopLeft(QPointF(3, 2 + KEY_TRAVEL * self._travel))

        clearance = 1 - self._travel
        for spread, alpha in CONTACT_SHADOW:
            contact = cap.adjusted(-spread, -0.3, spread, spread)
            contact.translate(0.25 + 0.65 * clearance, 0.65 + 1.7 * clearance)
            p.setBrush(QColor(28, 27, 23, round(alpha * (0.43 + 0.57 * clearance))))
            p.drawRoundedRect(contact, radius + spread / 2, radius + spread / 2)
        skirt = 0.65 + 1.8 * clearance
        p.setBrush(QColor(SELECTION if checked_face else KEY_SKIRT))
        p.drawRoundedRect(cap.translated(0.35, skirt), radius, radius)

        tone = KEY_PRIMARY if self.primary else KEY_CAP
        relief = 23 + 5 * (self.underMouse() and self.isEnabled()) - round(9 * self._travel)
        if checked_face:
            tone = KEY_PRESSED
        if not self.isEnabled():
            tone = KEY_DISABLED
            relief = 12
            p.setOpacity(.68)
        face = _surface(cap_width, cap_height, dpr, radius, 2.5, tone, relief)
        p.drawPixmap(cap.topLeft(), face)
        if self.hasFocus() and self._focus_visible:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(BUTTON_FACE if checked_face else INK), 1, Qt.PenStyle.DashLine))
            p.drawRoundedRect(cap.adjusted(3, 3, -3, -3), radius - 2, radius - 2)

        ink = QColor(KEY_DISABLED_INK if not self.isEnabled() else BUTTON_FACE if checked_face else INK)
        if self.glyph == "speaker":
            self._draw_speaker(p, cap.center(), ink, QColor(*tone))
        elif self.glyph == "headphones":
            if self.isEnabled():
                accent = QColor(SOLO_INK)
                ink = QColor.fromRgbF(*(a + (b - a) * self._solo_color for a, b in
                                        zip(ink.getRgbF()[:3], accent.getRgbF()[:3], strict=True)))
            self._draw_headphones(p, cap.center(), ink)
        elif self.glyph in ("play", "pause"):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
            center = cap.center()
            if self.glyph == "pause":
                for x in (-7., 2.):
                    p.drawRoundedRect(QRectF(center + QPointF(x, -8), center + QPointF(x + 4.5, 8)), 0.8, 0.8)
            else:
                p.drawPolygon(QPolygonF([center + QPointF(-5, -9), center + QPointF(9, 0), center + QPointF(-5, 9)]))
        else:
            p.setPen(ink)
            font = self.font()
            font.setWeight(font.Weight.DemiBold if self.primary else font.Weight.Medium)
            p.setFont(font)
            label_rect = cap.adjusted(0, 0, -14 if self.dropdown else 0, 0)
            p.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, self.text())
            if self.dropdown:
                x, y = cap.right() - 13, cap.center().y()
                pen = QPen(ink, 1.4)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawLine(QPointF(x - 3, y - 1.5), QPointF(x, y + 1.5))
                p.drawLine(QPointF(x, y + 1.5), QPointF(x + 3, y - 1.5))

    @staticmethod
    def _draw_headphones(painter: QPainter, center: QPointF, ink: QColor) -> None:
        painter.save()
        painter.translate(center)
        path = QPainterPath(QPointF(-8, 3))
        path.lineTo(-8, -1)
        path.cubicTo(-8, -12, 8, -12, 8, -1)
        path.lineTo(8, 3)
        painter.setPen(QPen(ink, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        for x in (-10.5, 5.):
            painter.drawRoundedRect(QRectF(x, .5, 5.5, 8.5), 2.3, 2.3)
        painter.restore()

    def _draw_speaker(self, painter: QPainter, center: QPointF, ink: QColor, face: QColor) -> None:
        painter.save()
        painter.translate(center)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawPolygon(QPolygonF([QPointF(-10, -3.5), QPointF(-6, -3.5), QPointF(-1, -8),
                                        QPointF(-1, 8), QPointF(-6, 3.5), QPointF(-10, 3.5)]))
        pen = QPen(ink, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setOpacity(painter.opacity() * (1 - self._mute_amount))
        for radius in (4, 8):
            arc = QPainterPath()
            rect = QRectF(1 - radius, -radius, 2 * radius, 2 * radius)
            arc.arcMoveTo(rect, -44)
            arc.arcTo(rect, -44, 88)
            painter.drawPath(arc)
        painter.restore()
        if self._mute_amount:
            painter.save()
            painter.translate(center)
            painter.setOpacity(painter.opacity() * self._mute_amount)
            painter.setPen(QPen(face, 3.5,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(-7, 9), QPointF(9, -9))
            painter.setPen(QPen(ink, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(-7, 9), QPointF(9, -9))
            painter.restore()
