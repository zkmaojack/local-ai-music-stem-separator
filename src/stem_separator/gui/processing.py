from enum import Enum, auto
from math import ceil, sin
from time import monotonic

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QStyle, QWidget

from stem_separator.gui.theme import ACCENT, SCREEN, SCREEN_EDGE, SCREEN_GRID, WAVEFORM, WAVEFORM_MUTED


class Stage(Enum):
    IDLE = auto()
    PREPARING = auto()
    ANALYZING = auto()
    LOADING = auto()
    SEPARATING = auto()
    FINALIZING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


ACTIVE = {Stage.PREPARING, Stage.ANALYZING, Stage.LOADING, Stage.SEPARATING, Stage.FINALIZING}
STAGES = {
    Stage.PREPARING: "音频准备",
    Stage.ANALYZING: "正在分析音频",
    Stage.LOADING: "正在加载模型",
    Stage.SEPARATING: "正在分离音轨",
    Stage.FINALIZING: "正在整理音轨",
}
PROGRESS_STAGES = {
    "decode": Stage.PREPARING, "analysis": Stage.ANALYZING, "load": Stage.LOADING,
    "inference": Stage.SEPARATING, "device": Stage.SEPARATING,
    "postprocess": Stage.FINALIZING, "export": Stage.FINALIZING,
}


class SignalDecomposition(QWidget):
    """The input profile drives a small painter-only processing display."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(115)
        self.setMaximumHeight(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("信号分解处理示意")
        self.stage = Stage.IDLE
        self.started = monotonic()
        self.transition_from = self.transition_to = 0.
        self.wave = QPainterPath()
        self.bands: tuple[float, ...] = ()
        self._grid = QPixmap()
        self._signal = QPixmap()
        self._scan_pen = QPen(QColor(ACCENT), 1)
        self._idle_pen = QPen(QColor(WAVEFORM_MUTED), 1)
        self._analysis_brushes = QColor(WAVEFORM_MUTED), QColor(WAVEFORM)
        self._speed = 1 / 60
        self._deadline = 0.
        self._ticks = 0
        self._window_started = 0.
        self._retry_at = 0.
        self.timer = QTimer(self, singleShot=True, timerType=Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.hide()

    def reset_signal(self) -> None:
        self.wave = QPainterPath()
        self._signal = QPixmap()
        self.bands = ()
        self.transition_from = self.transition_to = 0.

    def set_signal(self, profile: dict[str, list[float]]) -> None:
        points = profile["wave"]
        self.wave = QPainterPath()
        for index, height in enumerate(points):
            x = index / max(1, len(points) - 1)
            if index == 0:
                self.wave.moveTo(x, -height)
            else:
                self.wave.lineTo(x, -height)
        for index, height in reversed(list(enumerate(points))):
            self.wave.lineTo(index / max(1, len(points) - 1), height)
        if points:
            self.wave.closeSubpath()
        self.bands = tuple(profile["bands"])
        self._signal = QPixmap()
        self.update()

    def _split(self) -> float:
        fraction = min(1., (monotonic() - self.started) / .7)
        eased = fraction * fraction * (3 - 2 * fraction)
        return self.transition_from + (self.transition_to - self.transition_from) * eased

    def set_stage(self, stage: Stage) -> None:
        if self.stage != stage:
            self.transition_from = self._split()
            self.transition_to = 1. if stage in (Stage.SEPARATING, Stage.FINALIZING) else 0.
            self.stage = stage
            self.started = monotonic()
        self.setVisible(stage in ACTIVE)
        self._update_timer()
        self.update()

    def _update_timer(self) -> None:
        if self.stage in ACTIVE and self.isVisible() and self.style().styleHint(QStyle.StyleHint.SH_Widget_Animate):
            if not self.timer.isActive():
                self._deadline = monotonic()
                self._speed, self._ticks = 1 / 60, 0
                self._window_started = self._deadline
                self._schedule(self._deadline)
        else:
            self.timer.stop()

    def _schedule(self, now: float) -> None:
        self._deadline += self._speed
        if self._deadline <= now:
            self._deadline += (1 + int((now - self._deadline) / self._speed)) * self._speed
        self.timer.start(max(1, round(1000 * (self._deadline - now))))

    def _tick(self) -> None:
        now = monotonic()
        if self._speed < .02:
            self._ticks += 1
            if self._ticks == 30:
                # Ignore isolated Windows timer jitter; sustained missed deadlines matter.
                if now - self._window_started > .75:
                    self._speed, self._retry_at = 1 / 30, now + 4.
                self._ticks, self._window_started = 0, now
        elif now >= self._retry_at:
            self._speed = 1 / 60
            self._ticks, self._window_started = 0, now
        self.update()
        self._schedule(now)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_timer()

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:
        self._grid = QPixmap()
        self._signal = QPixmap()
        super().resizeEvent(event)

    def _pixmap(self, width: float, height: float) -> QPixmap:
        scale = self.devicePixelRatioF()
        image = QPixmap(max(1, ceil(width * scale)), max(1, ceil(height * scale)))
        image.setDevicePixelRatio(scale)
        return image

    def _draw_grid(self, bounds: QRectF) -> QPixmap:
        grid = self._pixmap(self.width(), self.height())
        grid.fill(QColor(SCREEN))
        painter = QPainter(grid)
        painter.setPen(QPen(QColor(SCREEN_GRID), 1))
        for column in range(13):
            x = bounds.left() + column * bounds.width() / 12
            painter.drawLine(QPointF(x, bounds.top()), QPointF(x, bounds.bottom()))
        for row in range(5):
            y = bounds.top() + row * bounds.height() / 4
            painter.drawLine(QPointF(bounds.left(), y), QPointF(bounds.right(), y))
        painter.end()
        return grid

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        bounds = QRectF(self.rect()).adjusted(16, 8, -16, -8)
        if self._grid.isNull() or self._grid.devicePixelRatio() != self.devicePixelRatioF():
            self._grid = self._draw_grid(bounds)
            self._signal = QPixmap()
        painter.drawPixmap(0, 0, self._grid)
        painter.setClipRect(bounds)
        time = monotonic() - self.started
        if self.stage == Stage.ANALYZING and self.bands:
            blend = min(1., time / .45) if self.timer.isActive() else 1.
            if blend < 1.:
                painter.save()
                self._draw_wave(painter, bounds, 0., time, alpha=1. - blend)
                painter.restore()
            painter.setOpacity(blend)
            self._draw_bands(painter, bounds, time)
            painter.setOpacity(1.)
        else:
            self._draw_wave(painter, bounds, self._split() if self.timer.isActive() else self.transition_to, time)
        if self.timer.isActive() and self.stage != Stage.FINALIZING:
            x = bounds.left() + bounds.width() * ((time / 5.) % 1.)
            painter.setPen(self._scan_pen)
            painter.drawLine(QPointF(x, bounds.top()), QPointF(x, bounds.top() + 7))

    def _draw_bands(self, painter: QPainter, bounds: QRectF, time: float) -> None:
        lane = bounds.width() / len(self.bands)
        painter.setPen(Qt.PenStyle.NoPen)
        for index, strength in enumerate(self.bands):
            painter.setBrush(self._analysis_brushes[bool(index % 4)])
            height = max(1., strength * bounds.height() * (.68 + .055 * sin(time * 2.2 + index * .45)))
            painter.drawRect(QRectF(bounds.left() + lane * (index + .2), bounds.bottom() - height,
                                   max(1., lane * .50), height))

    def _draw_wave(self, painter: QPainter, bounds: QRectF, split: float, time: float, *, alpha: float = 1.) -> None:
        if self.wave.isEmpty():
            painter.setPen(self._idle_pen)
            middle = bounds.center().y()
            painter.drawLine(QPointF(bounds.left(), middle), QPointF(bounds.right(), middle))
            return
        lanes = 1 if split < .015 else 5
        if self._signal.isNull():
            self._signal = self._render_wave(bounds)
        source = QRectF(self._signal.rect())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for lane in range(lanes):
            offset = (lane - (lanes - 1) / 2) * bounds.height() * .18 * split
            amplitude = bounds.height() * (.31 - .24 * split)
            movement = 1. if self.stage == Stage.FINALIZING else .96 + .04 * sin(time * 1.6 + lane)
            height = (2 * amplitude + 3) * movement
            painter.setOpacity(alpha * (1. if lane == lanes // 2 else .42 + .45 * split))
            painter.drawPixmap(QRectF(bounds.left(), bounds.center().y() + offset - height / 2,
                                      bounds.width(), height), self._signal, source)
        painter.setOpacity(1.)

    def _render_wave(self, bounds: QRectF) -> QPixmap:
        amplitude = bounds.height() * .31
        signal = self._pixmap(bounds.width(), 2 * amplitude + 3)
        signal.fill(Qt.GlobalColor.transparent)
        painter = QPainter(signal)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outline = QPen(QColor(WAVEFORM), .8)
        outline.setCosmetic(True)
        painter.setPen(outline)
        fill = QColor(WAVEFORM)
        fill.setAlpha(85)
        painter.setBrush(fill)
        painter.translate(0., amplitude + 1.5)
        painter.scale(bounds.width(), amplitude)
        painter.drawPath(self.wave)
        painter.end()
        return signal
