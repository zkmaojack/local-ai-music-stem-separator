from math import ceil

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSlider, QWidget

from stem_separator.gui.theme import WAVEFORM, WAVEFORM_MUTED, WAVEFORM_SOLO, technical_font

PLAYHEAD = "#e2e8e5"


class StatusLamp(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(12, 12)
        self.color = "#647268"

    def set_color(self, color: str) -> None:
        self.color = color
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#7b7e75"))
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

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # Move the handle to the click before Qt starts dragging it.
            fraction = (event.position().x() - 8) / max(1, self.width() - 16)
            self.setValue(round(self.minimum() + fraction * (self.maximum() - self.minimum())))
        super().mousePressEvent(event)


def time_text(seconds: float) -> str:
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f"{minutes}:{seconds:02d}"


class Timeline(QWidget):
    seek = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self.duration = 0.0
        self.position = 0.0
        self.setFixedHeight(35)
        self.setMinimumWidth(260)
        self.setFont(technical_font(12))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击跳转到对应时间")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.seek.emit(event.position().x() / max(1, self.width() - 1))

    def paintEvent(self, _event) -> None:
        if not self.duration:
            return
        painter = QPainter(self)
        width = self.width() - 1
        bottom = self.height() - 1
        painter.setPen(QColor("#777777"))
        painter.drawLine(0, bottom, width, bottom)
        interval = next(step for step in (5, 10, 15, 30, 60, 120, 300, 600, 1800, ceil(self.duration))
                        if self.duration / step <= max(2, width // 78))
        times = [0, *range(interval, ceil(self.duration), interval)]
        if len(times) > 1 and (self.duration - times[-1]) / self.duration * width < 54:
            times.pop()
        pips = interval / (5 if interval % 5 == 0 else 2)
        painter.setPen(QColor("#4e5554"))
        for index in range(1, ceil(self.duration / pips)):
            x = round(index * pips / self.duration * width)
            painter.drawLine(x, bottom - 3, x, bottom)
        for time in [*times, self.duration]:
            x = round(time / self.duration * width)
            painter.setPen(QColor("#777777"))
            painter.drawLine(x, bottom - 6, x, bottom)
            painter.setPen(QColor("#cccccc"))
            label = time_text(round(time))
            text_width = painter.fontMetrics().horizontalAdvance(label)
            painter.drawText(max(0, min(x - text_width // 2, width - text_width)), bottom - 10, label)
        x = round(self.position * width)
        painter.setPen(QColor(PLAYHEAD))
        painter.drawLine(x, bottom - 6, x, bottom)
        painter.setBrush(QColor(PLAYHEAD))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(x, bottom - 7), 2, 2)


class Waveform(QWidget):
    seek = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self._samples: NDArray[np.float32] | tuple[()] = ()
        self._path = QPainterPath()
        self._stroke_width = 1.0
        self.position = 0.0
        self.audible = True
        self.solo = False
        self.setMinimumHeight(45)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击波形跳转")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.seek.emit(event.position().x() / max(1, self.width() - 1))

    def set_samples(self, samples: NDArray[np.float32] | tuple[()]) -> None:
        self._samples = samples
        self._build_path()
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._build_path()

    def _build_path(self) -> None:
        self._path = QPainterPath()
        if not len(self._samples):
            return
        peaks = self._samples
        width = max(1, self.width())
        if len(peaks) > width:
            peaks = np.maximum.reduceat(peaks, np.arange(width) * len(peaks) // width)
        step = (width - 1) / max(1, len(peaks) - 1)
        self._stroke_width = max(1.0, step)
        center = self.height() / 2
        for index, peak in enumerate(peaks):
            half = max(0.5, min(1.0, float(peak) ** 0.65) * (center - 7))
            x = index * step
            self._path.moveTo(x, center - half)
            self._path.lineTo(x, center + half)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        width, height = self.width(), self.height()
        center = height / 2
        painter.setPen(QColor("#343837"))
        painter.drawLine(0, round(center), width, round(center))
        if not self._path.isEmpty():
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            color = QColor(WAVEFORM_SOLO if self.solo else WAVEFORM if self.audible else WAVEFORM_MUTED)
            pen = QPen(color)
            pen.setWidthF(self._stroke_width)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawPath(self._path)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QColor(PLAYHEAD))
        x = round(self.position * (width - 1))
        painter.drawLine(x, 0, x, height)
