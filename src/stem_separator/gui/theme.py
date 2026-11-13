import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QImage, QPainter, QPalette, QPixmap

CHASSIS = "#dad8d2"
CONTROL = "#e5e2db"
SCREEN = "#1c1f21"
SCREEN_EDGE = "#343837"
WAVEFORM = "#b7bec0"
WAVEFORM_SOLO = "#e9f2f0"
WAVEFORM_MUTED = "#62696b"
INK = "#202322"


def technical_font(pixels: int) -> QFont:
    font = QFont("Consolas")
    font.setPixelSize(pixels)
    return font


def silver_palette() -> QPalette:
    palette = QPalette()
    for role, value in (
        (QPalette.ColorRole.Window, CHASSIS), (QPalette.ColorRole.WindowText, INK),
        (QPalette.ColorRole.Base, "#f0eee9"), (QPalette.ColorRole.AlternateBase, CONTROL),
        (QPalette.ColorRole.Text, INK), (QPalette.ColorRole.Button, CONTROL),
        (QPalette.ColorRole.ButtonText, INK), (QPalette.ColorRole.Highlight, "#404440"),
        (QPalette.ColorRole.HighlightedText, "#f8f8f8"),
    ):
        palette.setColor(role, QColor(value))
    return palette


def matte_tile() -> QPixmap:
    noise = np.random.default_rng(9).normal(0, 1.45, (160, 160))
    pixels = np.rint(np.array((218, 216, 210)) + noise[..., None]).clip(0, 255).astype(np.uint8)
    image = QImage(pixels.data, 160, 160, 480, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(image)

STYLE = """
QWidget { color: @ink; }
QFrame#deck, QFrame#songPanel {
    background: qlineargradient(x1:0,y1:0,x2:0.25,y2:1,stop:0 #ebe8e1,stop:1 #d9d6ce);
    border: 1px solid #b5b2a9; border-top-color: #f0eee8; border-bottom-color: #b4b0a6; border-radius: 7px;
}
QFrame#well { background: @screen; border: 3px solid #575b58;
    border-top-color: #343836; border-left-color: #414543; border-bottom-color: #a6a79f; border-radius: 7px; }
QFrame#well QLabel#stateTitle { color: #e6e9e6; }
QFrame#well QLabel#muted { color: #b7bfba; }
QScrollArea#display { background: @screen; border: 3px solid #575b58;
    border-top-color: #343836; border-left-color: #414543; border-bottom-color: #a6a79f; border-radius: 7px; }
QWidget#trackPanel { background: @screen; }
QFrame#dropzone { background: #e3e0d8; border: 1px dashed #989b94; border-radius: 8px; }
QFrame#dropzone[dragging="true"] { background: #efede6; border: 1px dashed #4a524c; }
QLabel#heading { font-size: 25px; font-weight: 600; }
QLabel#songName, QLabel#stateTitle { font-size: 21px; font-weight: 600; }
QLabel#trackName { color: #e2e6e5; font-size: 15px; font-weight: 500; }
QLabel#muted { color: #545a56; }
QLabel#error { color: #302e29; }
QLabel#importIcon { background: #d2d0c7; border: 1px solid #afaea5;
    border-bottom-color: #e8e5de; border-radius: 6px; color: #303531; font-size: 28px; }
QFrame#rule { background: @screenEdge; }
QSlider::groove:horizontal { height: 8px; background: #484d49; border: 1px solid #565c57;
    border-top: 2px solid #2d322f; border-bottom-color: #f0eee8; border-radius: 4px; }
QSlider::sub-page:horizontal { background: #a8aea9; border-radius: 2px; margin: 3px 2px; }
QSlider::handle:horizontal { width: 14px; margin: -6px 0; border-radius: 3px;
    border: 1px solid #8f928b; border-top-color: #ebe9e1; border-bottom: 2px solid #858981;
    background: qlineargradient(x1:0,y1:0,x2:0.25,y2:1,stop:0 #e9e7df,stop:1 #c6c7bd); }
QSlider::handle:horizontal:pressed { margin: -5px 0 -7px; border-bottom-width: 1px; }
QSlider:focus { outline: 1px dashed #505650; }
QProgressBar { min-height: 10px; max-height: 10px; background: #343936; border: 1px solid #242825;
    border-bottom-color: #555e57; border-radius: 3px; }
QProgressBar::chunk { background: #c6cdc7; border-radius: 2px; }
QDialog, QFrame#instruments, QWidget#instrumentOptions { background: @control; }
QFrame#instruments { border: 1px solid #93948a; }
QLineEdit { min-height: 30px; background: #f0eee8; border: 1px solid #a4a59a; border-radius: 4px; padding: 0 9px; }
QPushButton#utility { min-height: 30px; padding: 0 8px; background: #d7d7ce; border: 1px solid #b3b5aa; border-radius: 4px; }
QPushButton#utility:hover { background: #eeece4; }
QPushButton#utility:checked, QPushButton#utility:pressed { color: #f0f2eb; background: #555d56; border-color: #4a514b; }
QPushButton#utility:focus { border: 1px dashed #303631; }
QPushButton#utility:checked:focus { border-color: #d9dfdb; }
QCheckBox { spacing: 10px; min-height: 30px; }
QToolTip { color: #202020; background: #ebebeb; border: 1px solid #888888; padding: 4px; }
QScrollBar:vertical { background: transparent; width: 11px; margin: 5px 2px; }
QScrollBar::handle:vertical { background: #787878; min-height: 26px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
""".replace("@ink", INK).replace("@screenEdge", SCREEN_EDGE).replace("@screen", SCREEN).replace("@control", CONTROL)


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor("#313131")))
    painter.drawRoundedRect(1, 1, 62, 62, 15, 15)
    painter.setBrush(QBrush(QColor("#e2e2e2")))
    for x, height in ((14, 16), (24, 28), (34, 36), (44, 21)):
        painter.drawRoundedRect(x, (64 - height) // 2, 5, height, 2, 2)
    painter.end()
    return QIcon(pixmap)
