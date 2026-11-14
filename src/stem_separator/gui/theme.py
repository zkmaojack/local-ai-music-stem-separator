import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QImage, QPainter, QPalette, QPixmap

CHASSIS = "#dad8d2"
CONTROL = "#e5e2db"
RAISED = "#f0eee9"
BUTTON_FACE = "#f4f1ea"
SCREEN = "#17191b"
SCREEN_SECONDARY = "#222528"
SCREEN_EDGE = "#343837"
SCREEN_GRID = "#303436"
WAVEFORM = "#b7bec0"
WAVEFORM_MUTED = "#62696b"
STEM_TONES = {
    "vocal": "#b9a9c5", "keys": "#c1ac84", "guitar": "#9eae93", "drums": "#c09b90",
    "synth": "#92b7bc", "strings": "#a9abbc", "winds": "#a0b6a4", "bass": "#a9aebe",
}
PLOT_CENTER = "#343a3b"
PLOT_CURSOR = "#e2e6df"
PLOT_HOVER = "#d89058"
SOLO_INK = "#aa4c20"
SOLO_LABEL = "#e5aa79"
SOLO_TINT = "#2e2824"
TRACK_DIMMED = "#909894"
INK = "#202322"
MUTED = "#565d59"
DISPLAY_TEXT = "#e6e8e7"
ACCENT = "#ce482c"
STATUS_GREEN = "#83b49c"
STATUS_IDLE = "#758178"
ERROR = "#a33c2a"
EDGE = "#aaa79d"
BEVEL = "#f4f1ea"
PANEL_BORDER = "#aaa79c"
PANEL_TOP = "#e9e6de"
PANEL_BOTTOM = "#dedbd3"
SCREEN_TOP = "#383b38"
SCREEN_LIP = "#aca99f"
SCROLL_THUMB = "#737972"
SELECTION = "#414945"
HOVER = "#d4d1c8"
DISABLED = "#797e7a"
SPACE = 4
RADIUS = 7
LIGHT_X, LIGHT_Y = -.48, -.63
CONTACT_SHADOW = ((2.1, 13), (1.25, 22), (.55, 41))
KEY_CAP = (221, 218, 210)
KEY_PRIMARY = (238, 235, 226)
KEY_PRESSED = (70, 77, 74)
KEY_DISABLED = (191, 192, 182)
KEY_DISABLED_INK = "#626661"
KEY_SKIRT = "#9a968a"
KEY_TRAVEL = 1.8
TOOLBAR_KEY_HEIGHT = 50
TRACK_KEY_SIZE = 44


def technical_font(pixels: int) -> QFont:
    font = QFont()
    font.setFamilies(["IBM Plex Mono", "JetBrains Mono", "Consolas", "Microsoft YaHei UI"])
    font.setPixelSize(pixels)
    return font


def interface_font() -> QFont:
    font = QFont()
    font.setFamilies(["Inter", "IBM Plex Sans", "Segoe UI", "Microsoft YaHei UI"])
    font.setPointSize(10)
    return font


def silver_palette() -> QPalette:
    palette = QPalette()
    for role, value in (
        (QPalette.ColorRole.Window, CHASSIS), (QPalette.ColorRole.WindowText, INK),
        (QPalette.ColorRole.Base, RAISED), (QPalette.ColorRole.AlternateBase, CONTROL),
        (QPalette.ColorRole.Text, INK), (QPalette.ColorRole.Button, CONTROL),
        (QPalette.ColorRole.ButtonText, INK), (QPalette.ColorRole.Highlight, SELECTION),
        (QPalette.ColorRole.HighlightedText, BUTTON_FACE), (QPalette.ColorRole.ToolTipBase, RAISED),
        (QPalette.ColorRole.ToolTipText, INK),
    ):
        palette.setColor(role, QColor(value))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(DISABLED))
    return palette


def matte_tile() -> QPixmap:
    noise = np.random.default_rng(9).normal(0, 1.45, (160, 160))
    pixels = np.rint(np.array((218, 216, 210)) + noise[..., None]).clip(0, 255).astype(np.uint8)
    image = QImage(pixels.data, 160, 160, 480, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(image)

STYLE = """
QWidget { color: @ink; }
QFrame#deck, QFrame#songPanel {
    background: qlineargradient(x1:0,y1:0,x2:0.25,y2:1,stop:0 @panelTop,stop:1 @panelBottom);
    border: 1px solid @panelBorder; border-top-color: @bevel; border-radius: @radius;
}
QFrame#well, QScrollArea#display { background: @screen; border: 2px solid @screenEdge;
    border-top-color: @screenTop; border-bottom-color: @screenLip; border-radius: @radius; }
QFrame#well QLabel#stateTitle { color: @displayText; }
QFrame#well QLabel#muted { color: @waveform; }
QWidget#trackPanel { background: @screen; }
QFrame#dropzone { background: #e3e0d8; border: 1px dashed #989b94; border-radius: 8px; }
QFrame#dropzone[dragging="true"] { background: #efede6; border: 1px dashed #4a524c; }
QLabel#heading { font-size: 25px; font-weight: 600; }
QLabel#songName, QLabel#stateTitle { font-size: 21px; font-weight: 600; }
QLabel#dialogTitle { font-size: 18px; font-weight: 600; }
QLabel#trackName { font-size: 15px; font-weight: 600; }
QLabel#time { color: @ink; }
QLabel#muted { color: @muted; }
QLabel#notice { color: @muted; }
QLabel#notice[severity="error"], QLabel[severity="error"] { color: @error; font-weight: 600; }
QLabel#importIcon { background: #d2d0c7; border: 1px solid #afaea5;
    border-bottom-color: #e8e5de; border-radius: 6px; color: #303531; font-size: 28px; }
QFrame#rule { background: @screenEdge; }
QProgressBar { min-height: 5px; max-height: 5px; background: @screenSecondary; border: 1px solid @screenEdge;
    border-radius: 2px; }
QProgressBar::chunk { background: @accent; border-radius: 2px; }
QDialog, QFrame#instruments, QWidget#instrumentOptions, QWidget#menuPanel { background: @control; }
QMenu, QFrame#instruments {
    background: @control; border: 1px solid @edge; border-top-color: @bevel;
    border-bottom: 2px solid @edge; border-radius: @radius; padding: 5px;
}
QMenu::item { min-height: 24px; padding: 4px 30px 4px 26px; border-radius: 3px; }
QMenu::item:selected { background: @selection; color: @button; }
QMenu::item:disabled { color: @disabled; }
QMenu::separator { height: 1px; background: @edge; margin: 5px 7px; }
QLineEdit, QComboBox, QSpinBox, QTextEdit {
    min-height: 28px; background: @raised; border: 1px solid @edge; border-radius: 4px; padding: 0 8px;
    selection-background-color: @selection; selection-color: @button;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus { border-color: @selection; }
QComboBox { padding-right: 25px; }
QComboBox::drop-down { width: 22px; background: @control; border-left: 1px solid @edge; }
QComboBox QAbstractItemView, QListView, QTreeView {
    background: @raised; alternate-background-color: @control; border: 1px solid @edge; border-radius: 4px;
    selection-background-color: @selection; selection-color: @button; outline: 0;
}
QTreeView::item, QListView::item { min-height: 24px; }
QHeaderView::section { background: @control; color: @muted; border: 0; border-bottom: 1px solid @edge; padding: 6px; }
QPushButton#utility, QDialogButtonBox QPushButton, QToolButton {
    min-height: 28px; padding: 0 8px; background: @raised; border: 1px solid @edge;
    border-top-color: @bevel; border-bottom-width: 2px; border-radius: 4px;
}
QPushButton#utility:hover, QDialogButtonBox QPushButton:hover, QToolButton:hover, QCheckBox:hover, QRadioButton:hover {
    background: @hover;
}
QPushButton#utility:checked, QToolButton:checked, QPushButton#utility:pressed,
QDialogButtonBox QPushButton:pressed, QToolButton:pressed {
    color: @button; background: @selection; border-color: @selection; padding-top: 1px;
}
QPushButton#utility:focus, QToolButton:focus, QCheckBox:focus, QRadioButton:focus { outline: 1px dashed @selection; }
QPushButton#utility:disabled, QDialogButtonBox QPushButton:disabled, QToolButton:disabled,
QComboBox:disabled { color: @disabled; background: @control; }
QCheckBox, QRadioButton { spacing: 9px; min-height: 27px; padding: 1px 4px; border-radius: 4px; }
QToolTip { color: @ink; background: @raised; border: 1px solid @panelBorder; padding: 6px 8px; }
QSplitter::handle { background: @control; width: 4px; }
QScrollBar:vertical { background: transparent; width: 11px; margin: 5px 2px; }
QScrollBar::handle:vertical { background: @scrollThumb; min-height: 26px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""
for token, value in {
    "screenSecondary": SCREEN_SECONDARY, "screenEdge": SCREEN_EDGE, "screen": SCREEN,
    "control": CONTROL, "displayText": DISPLAY_TEXT, "waveform": WAVEFORM, "ink": INK, "muted": MUTED,
    "error": ERROR, "accent": ACCENT, "edge": EDGE, "bevel": BEVEL, "radius": f"{RADIUS}px",
    "selection": SELECTION, "button": BUTTON_FACE, "disabled": DISABLED, "raised": RAISED, "hover": HOVER,
    "panelBorder": PANEL_BORDER, "panelTop": PANEL_TOP, "panelBottom": PANEL_BOTTOM,
    "screenTop": SCREEN_TOP, "screenLip": SCREEN_LIP, "scrollThumb": SCROLL_THUMB,
}.items():
    STYLE = STYLE.replace(f"@{token}", value)


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
