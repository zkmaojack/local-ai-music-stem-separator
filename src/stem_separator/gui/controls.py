from math import ceil

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRegion
from PySide6.QtWidgets import QLabel, QSlider, QWidget

from stem_separator.audio.playback import WaveformPeaks
from stem_separator.gui.hardware import _surface
from stem_separator.gui.theme import (
    BUTTON_FACE, DISPLAY_TEXT, EDGE, INK, KEY_PRIMARY, PLOT_CENTER, PLOT_CURSOR, PLOT_HOVER, SCREEN, SCREEN_EDGE,
    SELECTION, SOLO_LABEL, SOLO_TINT, STATUS_IDLE, TRACK_DIMMED, WAVEFORM, WAVEFORM_MUTED,
    technical_font,
)


class TrackName(QLabel):
    def __init__(self, name: str) -> None:
        super().__init__(name, objectName="trackName")
        self._tone = QColor(DISPLAY_TEXT)

    def set_mix_state(self, solo: bool, dim: float) -> None:
        tone = QColor(SOLO_LABEL if solo else DISPLAY_TEXT)
        dimmed = QColor(TRACK_DIMMED)
        tone = QColor.fromRgbF(*(a + (b - a) * dim for a, b in
                                zip(tone.getRgbF()[:3], dimmed.getRgbF()[:3], strict=True)))
        if self._tone != tone:
            self._tone = tone
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setPen(self._tone)
        painter.drawText(self.contentsRect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())


class StatusLamp(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(12, 12)
        self.color = STATUS_IDLE

    def set_color(self, color: str) -> None:
        self.color = color
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(EDGE))
        p.drawEllipse(QRectF(1, 1, 10, 10))
        lens = QLinearGradient(2, 2, 10, 10)
        lens.setColorAt(0, QColor(self.color).lighter(137))
        lens.setColorAt(1, QColor(self.color).darker(130))
        p.setBrush(lens)
        p.drawEllipse(QRectF(2, 2, 8, 8))


class ChannelSlider(QSlider):
    def __init__(self, description: str) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.setAccessibleName(description)
        self.setToolTip(description)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(30)

    @property
    def fraction(self) -> float:
        return (self.sliderPosition() - self.minimum()) / max(1, self.maximum() - self.minimum())

    def _move_to(self, x: float) -> None:
        self.setSliderPosition(self.minimum() + round(plot_fraction(x, self.width()) *
                                                      (self.maximum() - self.minimum())))

    def sliderChange(self, change) -> None:
        super().sliderChange(change)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self._move_to(event.position().x())
            self.update()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self.isSliderDown():
            self._move_to(event.position().x())

    def mouseReleaseEvent(self, event) -> None:
        if self.isSliderDown() and event.button() == Qt.MouseButton.LeftButton:
            self._move_to(event.position().x())
            self.setSliderDown(False)
            self.update()
            event.accept()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        left = plot_x(0, self.width())
        x = plot_x(self.fraction, self.width())
        right = plot_x(1, self.width())
        y = self.height() / 2
        trough = QRectF(left, y - 4, right - left, 8)
        rim = QLinearGradient(trough.topLeft(), trough.bottomLeft())
        rim.setColorAt(0, QColor("#262a28"))
        rim.setColorAt(1, QColor(BUTTON_FACE))
        painter.setPen(QPen(rim, 1))
        painter.setBrush(QColor(SCREEN_EDGE))
        painter.drawRoundedRect(trough, 3, 3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(EDGE))
        painter.drawRoundedRect(QRectF(left, y - 2, x - left, 4), 1, 1)
        handle = QRectF(x - 6, y - 10 + self.isSliderDown(), 12, 20)
        painter.setBrush(QColor(30, 30, 25, 42))
        painter.drawRoundedRect(handle.translated(1, 2).adjusted(-1, 0, 1, 1), 4, 4)
        painter.setBrush(QColor(EDGE))
        painter.drawRoundedRect(handle.translated(0, 1.5), 3, 3)
        painter.drawPixmap(handle.topLeft(), _surface(12, 20, self.devicePixelRatioF(), 3, 2.5, KEY_PRIMARY, 25))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(SELECTION), 1, Qt.PenStyle.DotLine))
            painter.drawRoundedRect(handle.adjusted(-2, -2, 2, 2), 2, 2)


PLOT_INSET = 8


def plot_x(fraction: float, width: int) -> float:
    return PLOT_INSET + min(1., max(0., fraction)) * max(1, width - 1 - 2 * PLOT_INSET)


def plot_fraction(x: float, width: int) -> float:
    return min(1., max(0., (x - PLOT_INSET) / (plot_x(1, width) - PLOT_INSET)))


def time_text(seconds: float) -> str:
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f"{minutes}:{seconds:02d}"


class AudioPlot(QWidget):
    seek = Signal(float)
    hovered = Signal(QPointF)
    hover_left = Signal()
    geometry_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.position = 0.0
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.seek.emit(plot_fraction(event.position().x(), self.width()))

    def enterEvent(self, event) -> None:
        self.hovered.emit(event.globalPosition())
        super().enterEvent(event)

    def mouseMoveEvent(self, event) -> None:
        self.hovered.emit(event.globalPosition())

    def leaveEvent(self, event) -> None:
        self.hover_left.emit()
        super().leaveEvent(event)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self.geometry_changed.emit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.geometry_changed.emit()

    def set_position(self, fraction: float) -> None:
        if fraction == self.position:
            return
        old = round(plot_x(self.position, self.width()))
        new = round(plot_x(fraction, self.width()))
        self.position = fraction
        for x in {old, new}:
            self.update(QRect(x - 4, 0, 9, self.height()))


class Timeline(AudioPlot):
    def __init__(self) -> None:
        super().__init__()
        self.duration = 0.0
        self.setFixedHeight(49)
        self.setMinimumWidth(260)
        self.setFont(technical_font(12))
        self.setToolTip("点击跳转到对应时间")

    def paintEvent(self, _event) -> None:
        if not self.duration:
            return
        painter = QPainter(self)
        left, right = round(plot_x(0, self.width())), round(plot_x(1, self.width()))
        width = right - left
        bottom = self.height() - 1
        painter.setPen(QColor(WAVEFORM_MUTED))
        painter.drawLine(left, bottom, right, bottom)
        interval = next(step for step in (5, 10, 15, 30, 60, 120, 300, 600, 1800, ceil(self.duration))
                        if self.duration / step <= max(2, width // 78))
        times = [0, *range(interval, ceil(self.duration), interval)]
        if len(times) > 1 and (self.duration - times[-1]) / self.duration * width < 54:
            times.pop()
        pips = interval / (5 if interval % 5 == 0 else 2)
        painter.setPen(QColor(SELECTION))
        for index in range(1, ceil(self.duration / pips)):
            x = round(plot_x(index * pips / self.duration, self.width()))
            painter.drawLine(x, bottom - 3, x, bottom)
        for time in [*times, self.duration]:
            x = round(plot_x(time / self.duration, self.width()))
            painter.setPen(QColor(WAVEFORM_MUTED))
            painter.drawLine(x, bottom - 6, x, bottom)
            painter.setPen(QColor(WAVEFORM))
            label = time_text(round(time))
            text_width = painter.fontMetrics().horizontalAdvance(label)
            painter.drawText(max(left, min(x - text_width // 2, right - text_width)), bottom - 10, label)
        x = plot_x(self.position, self.width())
        painter.setPen(QColor(PLOT_CURSOR))
        painter.drawLine(QPointF(x, bottom - 6), QPointF(x, bottom))
        painter.setBrush(QColor(PLOT_CURSOR))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(x, bottom - 7), 2, 2)


class TimelineHover(QWidget):
    """Read-only cursor across the scroll viewport, independent of the playback position."""

    def __init__(self, viewport: QWidget) -> None:
        super().__init__(viewport)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFont(technical_font(12))
        self.fraction: float | None = None
        self.timestamp = ""
        self._x = self._ruler_y = 0.
        self._label = QRectF()
        self.hide()

    def show_position(self, fraction: float, timeline: Timeline) -> None:
        old = self._dirty()
        self.fraction = fraction
        tenths = round(fraction * timeline.duration * 10)
        seconds, decimal = divmod(tenths, 10)
        self.timestamp = f"{time_text(seconds)}.{decimal}"
        origin = timeline.mapTo(self.parentWidget(), QPoint())
        left = origin.x() + plot_x(0, timeline.width())
        right = origin.x() + plot_x(1, timeline.width())
        self._x = origin.x() + plot_x(fraction, timeline.width())
        self._ruler_y = max(23., origin.y() + timeline.height() - 1)
        width = self.fontMetrics().horizontalAdvance(self.timestamp) + 14
        x = min(max(left, self._x - width / 2), right - width)
        label_y = max(1., origin.y() + 1.)
        self._label = QRectF(x, label_y, width, 19)
        if self.isHidden():
            self.show()
            self.raise_()
        self.update(old.united(self._dirty()))

    def clear(self) -> None:
        if self.fraction is not None:
            self.fraction = None
            self.timestamp = ""
            self.hide()

    def _dirty(self) -> QRegion:
        if self.fraction is None:
            return QRegion()
        line = QRect(round(self._x) - 7, round(self._ruler_y) - 9, 15, self.height())
        return QRegion(line).united(self._label.toAlignedRect().adjusted(-2, -2, 2, 2))

    def paintEvent(self, _event) -> None:
        if self.fraction is None:
            return
        painter = QPainter(self)
        color = QColor(PLOT_HOVER)
        painter.setPen(QPen(color, 1))
        painter.drawLine(QPointF(self._x, self._ruler_y), QPointF(self._x, self.height()))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF([
            QPointF(self._x - 3.5, self._ruler_y - 4), QPointF(self._x + 3.5, self._ruler_y - 4),
            QPointF(self._x, self._ruler_y + 3),
        ]))
        painter.setPen(QPen(color, 1))
        painter.setBrush(QColor(BUTTON_FACE))
        painter.drawRoundedRect(self._label, 4, 4)
        painter.setPen(QColor(INK))
        painter.drawText(self._label, Qt.AlignmentFlag.AlignCenter, self.timestamp)


class Waveform(AudioPlot):
    def __init__(self, tone: str = WAVEFORM) -> None:
        super().__init__()
        self.tone = QColor(tone)
        self._samples: WaveformPeaks | None = None
        self._envelope = QPainterPath()
        self._energy = QPainterPath()
        self._surface = QPixmap()
        self._audible = True
        self._solo = False
        self._dim = 0.
        self.setMinimumHeight(48)

    def set_samples(self, samples: WaveformPeaks | None) -> None:
        self._samples = samples
        self._build_path()
        self._surface = QPixmap()
        self.update()

    def set_mix_state(self, audible: bool, solo: bool, dim: float) -> None:
        if (audible, solo, dim) != (self._audible, self._solo, self._dim):
            self._audible, self._solo, self._dim = audible, solo, dim
            self._surface = QPixmap()
            self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._build_path()
        self._surface = QPixmap()

    def _build_path(self) -> None:
        self._envelope = QPainterPath()
        self._energy = QPainterPath()
        if self._samples is None:
            return
        left, right = plot_x(0, self.width()), plot_x(1, self.width())
        x, low, high, rms = self._samples.columns(int(right - left) + 1)
        center = self.height() / 2
        amplitude = center - 5
        xs = [left, *(left + x * (right - left)), right]
        top = high.clip(0, 1)
        bottom = low.clip(-1, 0)
        # Keep the RMS layer inside the signed peak envelope on the same linear amplitude scale.
        self._envelope = self._band(xs, top, bottom, center, amplitude)
        self._energy = self._band(xs, rms.clip(0, top), (-rms).clip(bottom, 0), center, amplitude)

    @staticmethod
    def _band(xs, top, bottom, center: float, amplitude: float) -> QPainterPath:
        upper = [top[0], *top, top[-1]]
        lower = [bottom[0], *bottom, bottom[-1]]
        path = QPainterPath(QPointF(xs[0], center - upper[0] * amplitude))
        for x, value in zip(xs[1:], upper[1:], strict=True):
            path.lineTo(x, center - value * amplitude)
        for x, value in zip(reversed(xs), reversed(lower), strict=True):
            path.lineTo(x, center - value * amplitude)
        path.closeSubpath()
        return path

    def _render_signal(self, scale: float) -> QPixmap:
        width, height = self.width(), self.height()
        surface = QPixmap(ceil(width * scale), ceil(height * scale))
        surface.setDevicePixelRatio(scale)
        surface.fill(QColor(SOLO_TINT if self._solo else SCREEN))
        painter = QPainter(surface)
        center = height / 2
        left, right = round(plot_x(0, width)), round(plot_x(1, width))
        painter.setPen(QColor(PLOT_CENTER))
        painter.drawLine(left, round(center), right, round(center))
        if not self._envelope.isEmpty():
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            subdued = QColor(WAVEFORM_MUTED)
            tone = self.tone if self._audible else subdued
            tone = QColor.fromRgbF(*(a + (b - a) * self._dim
                                     for a, b in zip(tone.getRgbF()[:3], subdued.getRgbF()[:3], strict=True)))
            base = 1. if self._audible else .6
            strength = base + (.52 - base) * self._dim
            fill = QColor(tone)
            fill.setAlpha(round(47 * strength))
            outline = QColor(tone)
            outline.setAlpha(round((190 if self._solo else 131) * strength))
            painter.setPen(QPen(outline, .75))
            painter.setBrush(fill)
            painter.drawPath(self._envelope)
            tone.setAlpha(round((232 if self._solo else 193) * strength))
            painter.fillPath(self._energy, tone)
        painter.end()
        return surface

    def paintEvent(self, _event) -> None:
        scale = self.devicePixelRatioF()
        if self._surface.isNull() or self._surface.devicePixelRatio() != scale:
            self._surface = self._render_signal(scale)
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._surface)
        painter.setPen(QColor(PLOT_CURSOR))
        x = plot_x(self.position, self.width())
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
