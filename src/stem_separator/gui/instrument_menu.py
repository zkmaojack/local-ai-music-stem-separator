from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from stem_separator.application.instruments import INSTRUMENTS, LABELS, validate_selection
from stem_separator.application.types import SeparationError
from stem_separator.gui.hardware import HardwareButton

PRIORITY = ("vocal", "drums", "bass", "guitar", "piano", "violin", "viola", "cello", "flute", "clarinet", "french-horn",
            "synth", "digital-piano", "riser", "impact", "whoosh", "kick", "snare", "hh", "toms")


class InstrumentMenu(QFrame):
    applied = Signal(tuple)

    def __init__(self, anchor: QWidget, selection: tuple[str, ...]) -> None:
        super().__init__(anchor, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setObjectName("instruments")
        self.setAccessibleName("选择乐器")
        self.anchor = anchor
        self.category = "常用"
        self.setFixedWidth(315)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 13, 14, 13)
        layout.setSpacing(8)
        self.search = QLineEdit(placeholderText="搜索乐器", accessibleName="搜索乐器")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        tabs = QHBoxLayout()
        tabs.setSpacing(5)
        self.group = QButtonGroup(self)
        for category in ("常用", "古典", "电子"):
            key = QPushButton(category, objectName="utility")
            key.setCheckable(True)
            self.group.addButton(key)
            key.setChecked(category == self.category)
            key.clicked.connect(lambda _checked, name=category: self._switch(name))
            tabs.addWidget(key, 1)
        layout.addLayout(tabs)
        self.caption = QLabel(objectName="muted")
        layout.addWidget(self.caption)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedHeight(215)
        options = QWidget()
        items = QVBoxLayout(options)
        items.setContentsMargins(2, 0, 4, 0)
        items.setSpacing(0)
        ordered = sorted(INSTRUMENTS, key=lambda item: PRIORITY.index(item.id) if item.id in PRIORITY else len(PRIORITY))
        self.options = []
        for instrument in ordered:
            checkbox = QCheckBox(instrument.label, checked=instrument.id in selection)
            checkbox.toggled.connect(self._update_selection)
            items.addWidget(checkbox)
            self.options.append((instrument, checkbox))
        self.no_matches = QLabel("没有匹配的乐器", objectName="muted")
        items.addWidget(self.no_matches)
        items.addStretch()
        scroll.setWidget(options)
        options.setObjectName("instrumentOptions")
        layout.addWidget(scroll)
        self.summary = QLabel(wordWrap=True)
        self.summary.setMinimumHeight(34)
        layout.addWidget(self.summary)
        buttons = QHBoxLayout()
        standard = QPushButton("标准四轨", objectName="utility")
        standard.clicked.connect(lambda: self._apply(()))
        self.apply_button = HardwareButton("应用", primary=True, width=104)
        self.apply_button.clicked.connect(lambda: self._apply(self.selection))
        buttons.addWidget(standard)
        buttons.addStretch()
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)
        self._filter()
        self._update_selection()
        if selection:
            self._switch(next(item.category for item in INSTRUMENTS if item.id == selection[0]))

    @property
    def selection(self) -> tuple[str, ...]:
        return tuple(item.id for item, checkbox in self.options if checkbox.isChecked())

    def open(self) -> None:
        self.adjustSize()
        anchor = self.anchor
        point = anchor.mapToGlobal(QPoint(anchor.width() - self.width(), anchor.height() + 4))
        screen = anchor.screen().availableGeometry()
        x = max(screen.left(), min(point.x(), screen.right() - self.width() + 1))
        y = min(point.y(), screen.bottom() - self.height() + 1)
        self.move(x, max(screen.top(), y))
        self.show()
        self.search.setFocus()

    def _switch(self, name: str) -> None:
        self.category = name
        for key in self.group.buttons():
            if key.text() == name:
                key.setChecked(True)
        self.search.clear()
        self._filter()

    def _filter(self) -> None:
        query = self.search.text().strip().casefold()
        self.caption.setText("搜索结果 · 实验性" if query else f"{self.category} · 实验性")
        count = 0
        for item, checkbox in self.options:
            visible = query in f"{item.label} {item.id}" if query else item.category == self.category
            checkbox.setVisible(visible)
            count += visible
        self.no_matches.setVisible(not count)

    def _update_selection(self) -> None:
        selected = self.selection
        try:
            validate_selection(selected)
        except SeparationError as error:
            self.summary.setText(str(error))
            self.apply_button.setEnabled(False)
            return
        self.summary.setText(f"已选 {len(selected)} 项" if selected else "当前使用标准四轨")
        self.summary.setToolTip("、".join(LABELS[name] for name in selected))
        self.apply_button.setEnabled(bool(selected))

    def _apply(self, selection: tuple[str, ...]) -> None:
        self.applied.emit(selection)
        self.close()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
