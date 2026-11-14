from PySide6.QtCore import QFileInfo, QPoint, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QFileDialog, QFileIconProvider, QMenu, QProxyStyle, QStyle, QWidget

from stem_separator.gui.theme import BUTTON_FACE, DISABLED, EDGE, MUTED, RAISED, SELECTION


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


class DeviceFileIcons(QFileIconProvider):
    def __init__(self) -> None:
        super().__init__()
        self.icons = {shape: self._make_icon(shape) for shape in ("file", "folder", "drive")}

    def icon(self, item) -> QIcon:
        if isinstance(item, QFileInfo):
            shape = "drive" if item.isRoot() else "folder" if item.isDir() else "file"
        elif item == self.IconType.File:
            shape = "file"
        elif item == self.IconType.Folder:
            shape = "folder"
        else:
            shape = "drive"
        return self.icons[shape]

    @staticmethod
    def _make_icon(shape: str) -> QIcon:
        tile = QPixmap(32, 32)
        tile.setDevicePixelRatio(2)
        tile.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tile)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(MUTED), 1.15))
        if shape == "folder":
            painter.drawRoundedRect(1, 4, 14, 9, 1, 1)
            painter.drawLine(2, 4, 7, 4)
            painter.drawLine(2, 8, 14, 8)
        elif shape == "file":
            painter.drawRoundedRect(4, 2, 8, 12, 1, 1)
            painter.drawLine(6, 6, 10, 6)
            painter.drawLine(6, 9, 10, 9)
        else:
            painter.drawRoundedRect(1, 5, 14, 7, 1, 1)
            painter.drawPoint(12, 9)
        painter.end()
        return QIcon(tile)


class DeviceFileDialog(QFileDialog):
    def __init__(self, parent: QWidget, title: str, directory: str, filters: str = "") -> None:
        super().__init__(parent)
        self.setOption(QFileDialog.Option.DontUseNativeDialog)
        self.setOption(QFileDialog.Option.DontUseCustomDirectoryIcons)
        self.setSupportedSchemes(["file"])
        self._file_icons = DeviceFileIcons()
        self.setIconProvider(self._file_icons)
        self.setWindowTitle(title)
        self.setLabelText(QFileDialog.DialogLabel.LookIn, "位置")
        self.setLabelText(QFileDialog.DialogLabel.FileType, "文件类型")
        if directory:
            self.setDirectory(directory)
        if filters:
            self.setNameFilter(filters)
        self.setMinimumSize(620, 420)
