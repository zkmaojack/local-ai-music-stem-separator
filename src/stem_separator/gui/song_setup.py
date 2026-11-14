from dataclasses import dataclass
from pathlib import Path
from typing import cast

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from stem_separator.application.instruments import LABELS, validate_model_selection
from stem_separator.application.types import MODEL_LABELS, QUALITY_LABELS, ModelChoice, Quality, SeparationError
from stem_separator.gui.hardware import HardwareButton
from stem_separator.gui.instrument_menu import InstrumentMenu
from stem_separator.gui.surfaces import DeviceDialog
from stem_separator.gui.theme import INK, SPACE
from stem_separator.storage.model_cache import assets_for_targets, recognition_assets


@dataclass(frozen=True)
class SongSetup:
    source: Path
    quality: Quality
    model: ModelChoice
    targets: tuple[str, ...] | None  # None runs recognition; () selects the standard four tracks.


class SetupChoice(QComboBox):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(INK), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        x, y = self.width() - 11, self.height() / 2
        painter.drawLine(QPointF(x - 3, y - 1), QPointF(x, y + 2))
        painter.drawLine(QPointF(x, y + 2), QPointF(x + 3, y - 1))


class SongSetupDialog(DeviceDialog):
    def __init__(self, parent: QWidget, source: Path, model_dir: Path) -> None:
        super().__init__(parent, "分离设置")
        self.source = source
        self.model_dir = model_dir
        self.targets: tuple[str, ...] = ()
        self.setMinimumWidth(470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(4 * SPACE,) * 4)
        layout.setSpacing(3 * SPACE)
        name = QLabel(source.name, objectName="dialogTitle", wordWrap=True)
        name.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(name)
        fields = QGridLayout()
        fields.setSpacing(2 * SPACE)
        self.model = SetupChoice()
        self.quality = SetupChoice()
        self.mode = SetupChoice()
        for key, label in MODEL_LABELS.items():
            self.model.addItem(label, key)
        for key, label in QUALITY_LABELS.items():
            self.quality.addItem(label, key)
        self.quality.setCurrentIndex(self.quality.findData("balanced"))
        for key, label in (("auto", "自动识别乐器"), ("manual", "手动选择乐器"), ("standard", "标准四轨")):
            self.mode.addItem(label, key)
        for index, (text, widget) in enumerate((("分离模型", self.model), ("分离质量", self.quality), ("音轨", self.mode))):
            label = QLabel(text)
            label.setBuddy(widget)
            widget.setAccessibleName(text)
            fields.addWidget(label, index, 0)
            fields.addWidget(widget, index, 1)
            widget.currentIndexChanged.connect(self._validate)
        fields.setColumnStretch(1, 1)
        layout.addLayout(fields)
        self.instruments = HardwareButton("选择乐器", dropdown=True)
        self.instruments.clicked.connect(self._pick_instruments)
        layout.addWidget(self.instruments)
        self.detail = QLabel(wordWrap=True, objectName="muted")
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.detail)
        row = QHBoxLayout()
        row.addStretch()
        cancel = HardwareButton("取消")
        cancel.clicked.connect(self.reject)
        self.confirm = HardwareButton("开始分离", primary=True)
        self.confirm.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.confirm)
        layout.addLayout(row)
        self._validate()

    @property
    def setup(self) -> SongSetup:
        mode = self.mode.currentData()
        targets = None if mode == "auto" else self.targets if mode == "manual" else ()
        return SongSetup(self.source, cast(Quality, self.quality.currentData()), cast(ModelChoice, self.model.currentData()), targets)

    def _pick_instruments(self) -> None:
        menu = InstrumentMenu(self.instruments, self.targets, show_standard=False)
        menu.applied.connect(self._select)
        menu.open()

    def _select(self, targets: tuple[str, ...]) -> None:
        self.targets = targets
        self._validate()

    def _validate(self) -> None:
        setup = self.setup
        manual = self.mode.currentData() == "manual"
        self.instruments.setVisible(manual)
        self.instruments.setText(f"乐器 {len(self.targets)} 项" if self.targets else "选择乐器")
        self.instruments.setToolTip("、".join(LABELS[track] for track in self.targets))
        message = ""
        try:
            if manual and not self.targets:
                raise SeparationError("请先选择乐器或音效。")
            if setup.targets is None:
                assets = recognition_assets(self.model_dir, "fast")
                if setup.model in ("demucs", "subroformer"):
                    assets += assets_for_targets(self.model_dir, (), setup.quality, setup.model)
            else:
                validate_model_selection(setup.targets, setup.model)
                assets = assets_for_targets(self.model_dir, setup.targets, setup.quality, setup.model)
            missing = [asset.path.name for asset in assets if not asset.path.is_file()]
            if missing:
                raise SeparationError("缺少本地模型文件\n" + "、".join(missing) + "\n请运行 stem-separator setup-model --all 备齐文件。")
        except SeparationError as error:
            message = str(error)
        self.confirm.setEnabled(not message)
        if not message and setup.targets is None and setup.model in ("demucs", "subroformer"):
            message = "自动识别人声、鼓和贝斯，其他声音合并到剩余轨。"
        self.detail.setText(message)
        self.detail.setVisible(bool(message))
