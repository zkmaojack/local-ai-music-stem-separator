from enum import Enum, auto

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QGridLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from stem_separator.gui.hardware import HardwareButton
from stem_separator.gui.surfaces import HardwarePanel
from stem_separator.gui.theme import DISPLAY_TEXT, SCREEN, SCREEN_EDGE, STATUS_GREEN, WAVEFORM, technical_font


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


STAGES = {
    Stage.PREPARING: "正在准备音频",
    Stage.ANALYZING: "正在识别乐器",
    Stage.LOADING: "正在加载模型",
    Stage.SEPARATING: "正在分离音轨",
    Stage.FINALIZING: "正在整理音轨",
}
PROGRESS_STAGES = {
    "decode": Stage.PREPARING, "analysis": Stage.ANALYZING, "load": Stage.LOADING,
    "inference": Stage.SEPARATING, "device": Stage.SEPARATING,
    "postprocess": Stage.FINALIZING, "export": Stage.FINALIZING,
}


class PrecisionProgress(QWidget):
    """Event-driven indicator; unknown progress never starts an animation timer."""

    def __init__(self) -> None:
        super().__init__()
        self.fraction: float | None = None
        self.setFixedHeight(14)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("当前阶段进度")

    def set_progress(self, completed: int | None = None, total: int | None = None) -> None:
        fraction = min(1., max(0., completed / total)) if completed is not None and total and total > 0 else None
        self.setAccessibleDescription("进度待定" if fraction is None else f"当前阶段 {fraction:.0%}")
        if fraction != self.fraction:
            self.fraction = fraction
            self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(.5, .5, -.5, -1)
        edge = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
        edge.setColorAt(0, QColor("#0d1010"))
        edge.setColorAt(1, QColor(SCREEN_EDGE))
        p.setPen(QPen(edge, 1))
        p.setBrush(QColor(SCREEN).darker(120))
        p.drawRoundedRect(bounds, 4, 4)
        inside = bounds.adjusted(2, 2, -2, -2)
        if self.fraction is None:
            # Static neutral ticks indicate an unmeasured stage, not partial completion.
            p.setPen(QPen(QColor(WAVEFORM).darker(170), 1))
            for x in range(8, int(bounds.right()) - 4, 12):
                p.drawLine(x, 5, x, 8)
        elif self.fraction > 0:
            inside.setWidth(inside.width() * self.fraction)
            fill = QLinearGradient(inside.topLeft(), inside.bottomLeft())
            fill.setColorAt(0, QColor(STATUS_GREEN).lighter(125))
            fill.setColorAt(.4, QColor(STATUS_GREEN))
            fill.setColorAt(1, QColor(STATUS_GREEN).darker(112))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(inside, 2, 2)
            p.setPen(QPen(QColor(DISPLAY_TEXT), .8))
            p.drawLine(inside.topRight(), inside.bottomRight())


class ProcessingPanel(HardwarePanel):
    def __init__(self) -> None:
        super().__init__(recessed=True, objectName="well")
        outer = QGridLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setColumnStretch(0, 1)
        outer.setColumnStretch(1, 4)
        outer.setColumnStretch(2, 1)
        outer.setRowStretch(0, 1)
        outer.setRowStretch(2, 1)
        self.body = QWidget()
        self.body.setMaximumWidth(720)
        self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        outer.addWidget(self.body, 1, 1)
        column = QVBoxLayout(self.body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(14)

        def label(text="", name="muted", pixels=None):
            widget = QLabel(text, objectName=name, alignment=Qt.AlignmentFlag.AlignCenter)
            widget.setTextFormat(Qt.TextFormat.PlainText)
            if pixels:
                widget.setFont(technical_font(pixels))
            return widget

        self.title = label("准备分离", "stateTitle")
        self.filename = label()
        self.filename.setMinimumWidth(0)
        self.filename.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._filename = ""
        self.detail = label()
        self.detail.setWordWrap(True)
        self.progress = PrecisionProgress()
        self.percentage = label("进度待定", pixels=20)
        self.model = label("模型 · 等待后台确认", pixels=12)
        self.device = label("设备 · 等待后台确认", pixels=12)
        self.model.setWordWrap(True)
        self.device.setWordWrap(True)
        self.cancel = HardwareButton("取消", width=96)
        column.addWidget(self.title)
        column.addWidget(self.filename)
        column.addSpacing(8)
        column.addWidget(self.detail)
        column.addWidget(self.progress)
        column.addWidget(self.percentage)
        column.addSpacing(4)
        column.addWidget(self.model)
        column.addWidget(self.device)
        column.addSpacing(6)
        column.addWidget(self.cancel, alignment=Qt.AlignmentFlag.AlignHCenter)

    def set_filename(self, filename: str) -> None:
        self._filename = filename
        self.filename.setToolTip(filename)
        self._fit_filename()

    def _fit_filename(self) -> None:
        self.filename.setText(self.filename.fontMetrics().elidedText(
            self._filename, Qt.TextElideMode.ElideMiddle, self.filename.width()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_filename()

    def set_progress(self, completed: int | None = None, total: int | None = None) -> None:
        self.progress.set_progress(completed, total)
        fraction = self.progress.fraction
        self.percentage.setText("进度待定" if fraction is None else f"{fraction:.0%}")
        self.percentage.setToolTip("当前阶段进度，后续阶段单独计算")
        self.progress.show()
        self.percentage.show()
