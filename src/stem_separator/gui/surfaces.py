from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QDialog, QFrame, QMenu, QProxyStyle, QStyle, QWidget

from stem_separator.gui.theme import (
    BEVEL, BUTTON_FACE, DISABLED, EDGE, PANEL_BOTTOM, PANEL_TOP, RAISED,
    SCREEN, SCREEN_EDGE, SELECTION,
)


class HardwarePanel(QFrame):
    """One upper-left light source for the chassis seams and display opening."""

    def __init__(self, *, recessed: bool = False, objectName: str = "") -> None:
        super().__init__(objectName=objectName)
        self.recessed = recessed

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -2)
        edge = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
        edge.setColorAt(0, QColor(EDGE if self.recessed else BEVEL))
        edge.setColorAt(1, QColor(BEVEL if self.recessed else EDGE))
        p.setPen(QPen(edge, 1.2))
        face = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
        face.setColorAt(0, QColor(SCREEN_EDGE if self.recessed else PANEL_TOP))
        face.setColorAt(1, QColor(SCREEN if self.recessed else PANEL_BOTTOM))
        p.setBrush(face)
        p.drawRoundedRect(bounds, 10, 10)
        if self.recessed:
            opening = bounds.adjusted(3, 3, -3, -3)
            p.setPen(QPen(QColor("#101212"), 1))
            p.setBrush(QColor(SCREEN))
            p.drawRoundedRect(opening, 7, 7)
            # Shadow remains within the bezel, leaving the waveform unobscured.
            p.setBrush(Qt.BrushStyle.NoBrush)
            for inset, alpha in ((1, 45), (2, 24), (3, 10)):
                shade = QLinearGradient(opening.topLeft(), opening.bottomRight())
                shade.setColorAt(0, QColor(0, 0, 0, alpha))
                shade.setColorAt(1, QColor(0, 0, 0, 0))
                p.setPen(QPen(shade, 1))
                p.drawRoundedRect(opening.adjusted(inset, inset, -inset, -inset), 6, 6)
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 45), 1))
            p.drawRoundedRect(bounds.adjusted(1.2, 1.2, -1.2, -1.2), 9, 9)


class DeviceStyle(QProxyStyle):
    """Draw small control indicators with the same geometry on every DPI."""

    def drawPrimitive(self, element, option, painter, widget=None) -> None:
        check = QStyle.PrimitiveElement.PE_IndicatorCheckBox
        radio = QStyle.PrimitiveElement.PE_IndicatorRadioButton
        arrows = {QStyle.PrimitiveElement.PE_IndicatorArrowDown: (0, 1),
                  QStyle.PrimitiveElement.PE_IndicatorArrowRight: (1, 0),
                  QStyle.PrimitiveElement.PE_IndicatorArrowUp: (0, -1),
                  QStyle.PrimitiveElement.PE_IndicatorArrowLeft: (-1, 0)}
        if element not in (check, radio) and element not in arrows:
            super().drawPrimitive(element, option, painter, widget)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = option.rect.center()
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        on = bool(option.state & QStyle.StateFlag.State_On)
        if element in arrows:
            dx, dy = arrows[element]
            role = option.palette.ColorRole.HighlightedText if option.state & QStyle.StateFlag.State_Selected else option.palette.ColorRole.Text
            painter.setPen(QPen(option.palette.color(role), 1.4))
            tip = center + QPoint(2 * dx, 2 * dy)
            painter.drawLine(center + QPoint(-2 * dx - 3 * dy, -2 * dy + 3 * dx), tip)
            painter.drawLine(center + QPoint(-2 * dx + 3 * dy, -2 * dy - 3 * dx), tip)
        else:
            painter.setPen(QPen(QColor(EDGE if enabled else DISABLED), 1))
            painter.setBrush(QColor(SELECTION if on else RAISED))
            box = option.rect.adjusted(1, 1, -1, -1)
            if element == radio:
                painter.drawEllipse(box)
            else:
                painter.drawRoundedRect(box, 3, 3)
            if on:
                pen = QPen(QColor(BUTTON_FACE), 1.6)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                if element == radio:
                    painter.drawPoint(center)
                else:
                    painter.drawLine(center + QPoint(-3, 0), center + QPoint(-1, 2))
                    painter.drawLine(center + QPoint(-1, 2), center + QPoint(4, -3))
        painter.restore()


class DeviceMenu(QMenu):
    def __init__(self, parent: QWidget, title: str = "") -> None:
        super().__init__(title, parent)
        self.setMinimumWidth(176)

    def submenu(self, title: str) -> "DeviceMenu":
        child = DeviceMenu(self, title)
        self.addMenu(child)
        return child

    def run_below(self, anchor: QWidget) -> None:
        self.run_at(anchor.mapToGlobal(anchor.rect().bottomLeft() + QPoint(0, 4)))

    def run_at(self, point: QPoint) -> None:
        self.exec(point)
        self.deleteLater()


class DeviceDialog(QDialog):
    def __init__(self, parent: QWidget, title: str) -> None:
        super().__init__(parent, windowTitle=title)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
