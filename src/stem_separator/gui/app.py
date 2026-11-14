import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
from tempfile import gettempdir
from threading import Event
from time import monotonic
from typing import cast

from PySide6.QtCore import QLibraryInfo, QSettings, QStandardPaths, Qt, QTimer, QTranslator, Slot
from PySide6.QtGui import QBrush, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QProgressBar, QScrollArea, QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)

from stem_separator.audio.playback import Preview, load_preview
from stem_separator.application.instruments import FOUR_STEM_TARGETS, LABELS, REMAINDER, validate_model_selection
from stem_separator.application.types import MODEL_LABELS, QUALITY_LABELS, ModelChoice, Quality, SeparationError
from stem_separator.gui.audio_cache import AudioCache
from stem_separator.gui.controls import StatusLamp, time_text
from stem_separator.gui.hardware import HardwareButton
from stem_separator.gui.instrument_menu import InstrumentMenu
from stem_separator.gui.jobs import BackgroundTask, SeparationJob
from stem_separator.gui.processing import PROGRESS_STAGES, STAGES, SignalDecomposition, Stage
from stem_separator.gui.results import Results
from stem_separator.gui.song_setup import SongSetup, SongSetupDialog
from stem_separator.gui.surfaces import DeviceDialog, DeviceFileDialog, DeviceMenu, DeviceStyle
from stem_separator.gui.theme import (
    ACCENT, ERROR, SPACE, STATUS_GREEN, STATUS_IDLE, STYLE, app_icon, interface_font, matte_tile,
    silver_palette, technical_font,
)
from stem_separator.storage.export import copy_stems
from stem_separator.storage.model_cache import assets_for_targets, recognition_assets

FORMATS = (".wav", ".mp3", ".flac", ".m4a", ".ogg")


@dataclass
class PreviousResult:
    setup: SongSetup
    cache: AudioCache
    paths: dict[str, Path]
    result_targets: tuple[str, ...]
    result_quality: Quality | None
    selection_recognized: bool
    manual: bool
    uncertain: tuple[str, ...]
    metadata: str
    primary_text: str
    notice: str
    notice_error: bool
    resume: bool


class MainWindow(QMainWindow):
    def __init__(self, model_dir: Path) -> None:
        super().__init__()
        self.setWindowTitle("音轨分离")
        self.setMinimumSize(720, 540)
        self.resize(1080, 710)
        self.setAcceptDrops(True)
        self.model_dir = model_dir.resolve()
        self.cache_root = Path(gettempdir()) / "local-stem-separator-audio"
        AudioCache.recover(self.cache_root)
        self.source: Path | None = None
        self.paths: dict[str, Path] = {}
        self.session: AudioCache | None = None
        self.busy = False
        self.close_pending = False
        self.phase = ""
        self.job_result = None
        self.job_error = ""
        self.progress_started = 0.
        self.progress_total = 0
        self.targets: tuple[str, ...] = ()
        self.quality: Quality = "balanced"
        self.model: ModelChoice = "auto"
        self.result_targets: tuple[str, ...] = ()
        self.result_quality: Quality | None = None
        self.recognized = False
        self.manual_targets = False
        self.pending_setup: SongSetup | None = None
        self.previous: PreviousResult | None = None
        self.choosing_song = False
        self.preview_cancel = Event()
        QSettings("Local Stem Separator", "Preferences").remove("display/reducedMotion")
        self.processing_stage = Stage.IDLE
        self.uncertain_targets: tuple[str, ...] = ()
        self.job = SeparationJob(self)
        self.job.event.connect(self._on_event)
        self.job.finished.connect(self._on_finished)

        chassis = QWidget(objectName="chassis")
        palette = chassis.palette()
        palette.setBrush(QPalette.ColorRole.Window, QBrush(matte_tile()))
        chassis.setPalette(palette)
        chassis.setAutoFillBackground(True)
        self.setCentralWidget(chassis)
        layout = QVBoxLayout(chassis)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)
        self.pages = QStackedWidget()
        self.empty = self._build_empty()
        self.workspace = self._build_workspace()
        self.pages.addWidget(self.empty)
        self.pages.addWidget(self.workspace)
        self.notice = QLabel(objectName="notice", wordWrap=True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.hide()
        layout.addWidget(self.pages, 1)
        layout.addWidget(self.notice)

        open_shortcut = QShortcut(QKeySequence.StandardKey.Open, self)
        open_shortcut.activated.connect(self.choose_file)

    def _build_empty(self) -> QWidget:
        empty = QWidget()
        layout = QVBoxLayout(empty)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self.dropzone = QFrame(objectName="dropzone")
        content = QVBoxLayout(self.dropzone)
        content.setSpacing(17)
        content.addStretch(1)
        icon = QLabel("♪", objectName="importIcon", alignment=Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(65, 65)
        heading = QLabel("把歌曲拖到这里", objectName="heading")
        pick = HardwareButton("选择歌曲", primary=True, width=158)
        pick.setFixedHeight(51)
        pick.clicked.connect(self.choose_file)
        for widget in (icon, heading, pick,
                       QLabel("支持 WAV、MP3、FLAC", objectName="muted")):
            content.addWidget(widget, alignment=Qt.AlignmentFlag.AlignHCenter)
        content.addStretch(1)
        layout.addWidget(self.dropzone, 1)
        layout.addWidget(QLabel("音频仅在本机处理", objectName="muted"), alignment=Qt.AlignmentFlag.AlignHCenter)
        return empty

    def _build_workspace(self) -> QWidget:
        workspace = QWidget()
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        header = QFrame(objectName="songPanel")
        self.header_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, header)
        self.header_layout.setContentsMargins(14, 12, 14, 12)
        self.header_layout.setSpacing(12)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.header_layout.addLayout(row, 1)
        icon = QLabel("♪", objectName="importIcon", alignment=Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(48, 48)
        row.addWidget(icon)
        details = QVBoxLayout()
        details.setSpacing(3)
        self.song = QLabel(objectName="songName")
        self.song.setTextFormat(Qt.TextFormat.PlainText)
        self.song.setMinimumWidth(1)
        self.metadata = QLabel(objectName="muted")
        self.metadata.setFont(technical_font(13))
        details.addWidget(self.song)
        details.addWidget(self.metadata)
        row.addLayout(details, 1)
        self.lamp = StatusLamp()
        self.engine = QLabel("本地处理", objectName="muted")
        self.engine.setFixedWidth(88)
        self.engine.setAccessibleName("处理状态")
        row.addWidget(self.lamp)
        row.addWidget(self.engine)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.change = HardwareButton("换首歌", width=93)
        self.change.clicked.connect(self.choose_file)
        self.instruments = HardwareButton("选择乐器", width=106, dropdown=True)
        self.instruments.clicked.connect(self._choose_instruments)
        self.tracks = HardwareButton("音轨", dropdown=True)
        self.tracks.clicked.connect(self._choose_visibility)
        self.tracks.setEnabled(False)
        self.quality_button = HardwareButton("均衡", dropdown=True)
        self.quality_button.clicked.connect(lambda: self._choose_quality(self.quality_button))
        self.primary = HardwareButton("开始分离", primary=True, width=112)
        self.primary.clicked.connect(self._primary_action)
        for button in (self.change, self.instruments, self.tracks, self.quality_button, self.primary):
            actions.addWidget(button)
        self.header_actions = actions
        self.header_layout.addLayout(actions)
        self.header_layout.setAlignment(actions, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(header)

        self.content = QStackedWidget()
        panel = QFrame(objectName="well")
        state = QVBoxLayout(panel)
        state.setContentsMargins(6 * SPACE, 4 * SPACE, 6 * SPACE, 4 * SPACE)
        state.setSpacing(2 * SPACE)
        state.addStretch(1)
        self.state_title = QLabel("准备分离", objectName="stateTitle", alignment=Qt.AlignmentFlag.AlignCenter)
        self.state_detail = QLabel("人声、鼓、贝斯和其他", objectName="muted",
                                   alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.progress = QProgressBar(textVisible=False)
        self.progress.setFixedWidth(310)
        self.progress.setAccessibleName("分离进度")
        self.signal_display = SignalDecomposition()
        self.signal_caption = QLabel("信号分解示意 · 依据原始音频", objectName="muted", alignment=Qt.AlignmentFlag.AlignCenter)
        self.signal_caption.setFont(technical_font(11))
        self.processing_model = QLabel(objectName="muted", alignment=Qt.AlignmentFlag.AlignCenter)
        self.processing_model.setFont(technical_font(12))
        self.signal_caption.hide()
        self.processing_model.hide()
        self.cancel = HardwareButton("取消", width=88)
        self.cancel.clicked.connect(self._cancel)
        state.addWidget(self.state_title)
        state.addWidget(self.signal_display, 3)
        state.addWidget(self.signal_caption)
        state.addWidget(self.processing_model)
        state.addWidget(self.state_detail)
        state.addWidget(self.progress, alignment=Qt.AlignmentFlag.AlignHCenter)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.cancel)
        buttons.addStretch()
        state.addLayout(buttons)
        state.addStretch(1)
        self.results = Results()
        self.results.audio_error.connect(lambda detail: self._show_notice(f"无法播放，请检查系统音频输出。{detail}"))
        self.content.addWidget(panel)
        self.content.addWidget(self.results)
        layout.addWidget(self.content, 1)
        self._status("准备分离", "人声、鼓、贝斯和其他")
        return workspace

    def _status(self, title: str, detail: str, *, cancellable: bool = False) -> None:
        self.content.setCurrentIndex(0)
        self.state_title.setText(title)
        self.state_detail.setText(detail)
        self.progress.hide()
        self.cancel.setVisible(cancellable)
        self.cancel.setEnabled(cancellable)

    def _show_stage(self, stage: Stage) -> None:
        if stage == self.processing_stage:
            return
        self.processing_stage = stage
        self.signal_display.set_stage(stage)
        active = stage in STAGES
        self.signal_caption.setVisible(active)
        self.processing_model.setVisible(active)
        if active:
            self.state_title.setText(STAGES[stage])
            split = stage in (Stage.SEPARATING, Stage.FINALIZING)
            self.signal_caption.setText("基于原始音频的分轨示意" if split else "信号分解示意 · 依据原始音频")
            self.progress.hide()

    def _show_notice(self, message: str = "", *, error: bool = True) -> None:
        self.notice.setProperty("severity", "error" if error else "info")
        self.notice.style().unpolish(self.notice)
        self.notice.style().polish(self.notice)
        self.notice.setText(message)
        self.notice.setVisible(bool(message))

    def _engine_status(self, label: str, color: str) -> None:
        self.engine.setText(label)
        self.lamp.set_color(color)

    @Slot()
    def choose_file(self) -> None:
        if self.choosing_song or (self.busy and not self._can_replace()):
            return
        start = str(self.source.parent) if self.source else ""
        filters = "音频文件 (" + " ".join(f"*{extension}" for extension in FORMATS) + ")"
        self.choosing_song = True
        try:
            filename, _ = QFileDialog.getOpenFileName(self, "选择歌曲", start, filters)
        finally:
            self.choosing_song = False
        if filename:
            self.set_source(Path(filename))

    def set_source(self, source: Path) -> None:
        if self.choosing_song:
            return
        if not source.is_file() or source.suffix.lower() not in FORMATS:
            self._show_notice("请选择一个本地 WAV、MP3、FLAC、M4A 或 Ogg 音频文件。")
            return
        if self.busy and not self._can_replace():
            return
        dialog = SongSetupDialog(self, source.resolve(), self.model_dir)
        self.choosing_song = True
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            setup = dialog.setup
        finally:
            self.choosing_song = False
            dialog.deleteLater()
        if accepted:
            self._begin_song(setup)

    def _begin_song(self, setup: SongSetup) -> None:
        if self.busy:
            if self._can_replace():
                self.pending_setup = setup
                self._cancel()
            return
        self._retain_result()
        self.signal_display.reset_signal()
        self.processing_model.clear()
        self._show_notice()
        self.source = setup.source
        self.quality, self.model = setup.quality, setup.model
        self._render_config()
        self.recognized = False
        self.manual_targets = setup.targets is not None
        self.uncertain_targets = ()
        self.targets = setup.targets or ()
        self._update_selection_button()
        self.song.setToolTip(self.source.name)
        self.pages.setCurrentWidget(self.workspace)
        self._fit_song_name()
        self.metadata.setText(self.source.suffix[1:].upper())
        self.primary.setText("开始分离")
        self._engine_status("本地处理", STATUS_IDLE)
        if self.manual_targets:
            self._prepare_separation()
        else:
            self._start_recognition()

    def _fit_song_name(self) -> None:
        if self.source:
            width = self.song.width()
            self.song.setText(self.song.fontMetrics().elidedText(self.source.name, Qt.TextElideMode.ElideMiddle, width))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "header_actions"):
            # Leave room for the song name, format and status before the controls wrap.
            one_row = event.size().width() >= self.header_actions.sizeHint().width() + 390
            direction = QBoxLayout.Direction.LeftToRight if one_row else QBoxLayout.Direction.TopToBottom
            self.header_layout.setDirection(direction)
        QTimer.singleShot(0, self._fit_song_name)

    @Slot()
    def _primary_action(self) -> None:
        if self.busy or self.source is None:
            return
        if self.paths and (self.recognized or self.manual_targets) and (
            self.targets, self.quality
        ) == (self.result_targets, self.result_quality):
            self._save()
        elif not self.recognized and not self.manual_targets:
            self._start_recognition()
        else:
            self._prepare_separation()

    def _start_recognition(self) -> None:
        if self._models_present(recognition_assets(self.model_dir, "fast")):
            self._retain_result()
            self._start_job("recognize", self.source)

    def _models_present(self, assets) -> bool:
        missing = [asset.path for asset in assets if not asset.path.is_file()]
        if not missing:
            return True
        self._model_error("缺少本地模型", "请将模型文件放入下列位置，然后重试。", "\n".join(map(str, missing)))
        return False

    def _model_error(self, title: str, message: str, detail: str, *, preserve_view: bool = False) -> None:
        self._show_stage(Stage.FAILED)
        if not preserve_view and not self._restore_result() and not self.paths:
            self._engine_status(title, ERROR)
            self._status(title, "安装所需模型后可重试。")
        dialog = DeviceDialog(self, title)
        dialog.setMinimumWidth(480)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(4 * SPACE, 4 * SPACE, 4 * SPACE, 4 * SPACE)
        layout.addWidget(QLabel(title, objectName="dialogTitle"))
        label = QLabel(message, wordWrap=True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(label)
        details = QTextEdit(readOnly=True)
        details.setPlainText(detail)
        details.setFont(technical_font(12))
        details.setFixedHeight(95)
        layout.addWidget(details)
        instructions = QLabel("可在联网电脑执行下列命令，再复制 .model-cache 到本机。", wordWrap=True, objectName="muted")
        layout.addWidget(instructions)
        command = QLabel("stem-separator setup-model --all")
        command.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        command.setFont(technical_font(13))
        layout.addWidget(command)
        ok = HardwareButton("关闭", primary=True)
        ok.clicked.connect(dialog.accept)
        layout.addWidget(ok, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()
        dialog.deleteLater()

    def _prepare_separation(self) -> None:
        try:
            validate_model_selection(self.targets, self.model)
        except SeparationError as error:
            self._restore_result()
            self._show_notice(str(error))
            return
        assets = assets_for_targets(self.model_dir, self.targets, self.quality, self.model)
        if self._models_present(assets):
            self.start_separation()

    def _choose_quality(self, anchor: QWidget) -> None:
        menu = DeviceMenu(anchor)
        descriptions = {"fast": "低资源", "balanced": "推荐", "high": "耗时较长"}
        for key, label in QUALITY_LABELS.items():
            action = menu.addAction(f"{label} · {descriptions[key]}")
            action.setCheckable(True)
            action.setChecked(key == self.quality)
            action.triggered.connect(lambda _checked, value=key: self._apply_quality(cast(Quality, value)))
        menu.run_below(anchor)

    def _render_config(self) -> None:
        self.quality_button.setText(QUALITY_LABELS[self.quality])
        self.quality_button.setToolTip(MODEL_LABELS[self.model])

    def _apply_quality(self, quality: Quality) -> None:
        if self.busy or quality == self.quality:
            return
        self.quality = quality
        self._render_config()
        if self.source is None:
            return
        if not self.manual_targets:
            self.recognized = False
        if self.paths:
            matches = ((self.recognized or self.manual_targets)
                       and (self.targets, self.quality) == (self.result_targets, self.result_quality))
            self.primary.setText("保存音轨" if matches else "重新分离")
            self._show_notice("" if matches else "点击「重新分离」可应用新的质量设置。", error=False)
        else:
            self.primary.setText("开始分离")

    @Slot()
    def _choose_visibility(self) -> None:
        menu = self.results.visibility_menu(self.tracks)
        menu.run_below(self.tracks)

    @Slot()
    def _choose_instruments(self) -> None:
        selection = self.targets if self.targets != (REMAINDER,) else ()
        menu = InstrumentMenu(self.instruments, selection, self.uncertain_targets)
        menu.applied.connect(self._apply_instruments)
        menu.open()

    @Slot(tuple)
    def _apply_instruments(self, targets: tuple[str, ...]) -> None:
        try:
            validate_model_selection(targets, self.model)
        except SeparationError as error:
            self._show_notice(str(error))
            return
        self.manual_targets = True
        self.targets = targets
        self._update_selection_button()
        self.primary.setText("保存音轨" if self.paths and (targets, self.quality) == (self.result_targets, self.result_quality)
                             else "重新分离" if self.paths else "开始分离")
        if not self.paths:
            self._status("准备分离", self._selection_detail())

    def _update_selection_button(self) -> None:
        count = len(self.targets) if self.targets != (REMAINDER,) else 0
        self.instruments.setText(f"乐器 {count} 项" if count else "选择乐器")
        self.instruments.setToolTip(self._selection_detail())

    def _selection_detail(self) -> str:
        if not self.targets:
            return "人声、鼓、贝斯和其他"
        if self.targets == (REMAINDER,):
            return "没有可靠识别到可分离的音轨"
        return "、".join(LABELS[name] for name in self.targets)

    def start_separation(self) -> None:
        if self.busy or self.source is None:
            return
        self._retain_result()
        try:
            self.session = AudioCache(self.cache_root)
        except OSError as error:
            self._restore_result()
            self._show_notice(f"无法创建临时目录。{error}")
            return
        selection = "manual" if self.manual_targets else "automatic"
        self._start_job("separate", self.source, self.session.path, selection, self.model, *self.targets)

    def _start_job(self, phase: str, *paths: Path | str) -> None:
        self.phase = phase
        self.job_error = ""
        self.job_result = None
        self.progress_started = 0.
        self.progress_total = 0
        self._show_notice()
        self._set_busy(True)
        titles = {"recognize": "本地分析", "separate": "正在分离"}
        self._engine_status(titles[phase], ACCENT)
        if phase == "recognize":
            self._status("音频准备", "识别完成后会自动分离。", cancellable=True)
        else:
            self._status("音频准备", "正在启动本地处理…", cancellable=True)
        self._show_stage(Stage.PREPARING)
        self.job.start(phase, self.model_dir, self.quality, *paths)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.primary.setEnabled(not busy)
        self.change.setEnabled(not busy or self.phase != "save")
        self.instruments.setEnabled(not busy)
        self.quality_button.setEnabled(not busy)
        self.tracks.setEnabled(not busy and bool(self.paths) and self.results.player is not None)

    @Slot(dict)
    def _on_event(self, event: dict) -> None:
        kind = event["event"]
        if kind == "error":
            self.job_error = event["message"]
        elif kind == "result":
            self.job_result = event
        elif kind == "progress" and not self.job.cancelled:
            stage = event["stage"]
            if duration := event.get("duration"):
                self.metadata.setText(f"{self.source.suffix[1:].upper()} · {time_text(round(duration))}")
            if signal := event.get("signal"):
                self.signal_display.set_signal(signal)
            if model := event.get("model"):
                engine = event.get("device")
                self.processing_model.setText(f"{model} · {engine}" if engine else model)
            if stage in PROGRESS_STAGES:
                self._show_stage(PROGRESS_STAGES[stage])
            detail = {"decode": "正在读取本地音频…", "load": "正在加载本地模型…",
                      "export": "正在生成试听文件…", "postprocess": "正在对齐和检查音轨…"}
            if stage in ("inference", "analysis"):
                done, total = event.get("completed"), event.get("total")
                device = "GPU" if event["message"].startswith("CUDA") else "CPU"
                title = f"{device} 处理中" if stage == "inference" else event["message"].rstrip("…")
                known = done is not None and bool(total)
                self.progress.setVisible(known)
                if not known:
                    self.state_detail.setText(f"{title}…" if stage == "inference" else event["message"])
                    return
                self.progress.setRange(0, total)
                self.progress.setValue(done)
                eta = self._estimate_remaining(done, total) if stage == "inference" else ""
                self.state_detail.setText(f"{title} · {round(100 * done / total)}%{eta}")
            elif stage == "device":
                self.progress.hide()
                self.progress_started = 0.
                self.state_detail.setText("显存不足，正在调整处理方式…")
            else:
                message = event["message"]
                if stage != "load" or not message.startswith("正在"):
                    message = detail.get(stage, message)
                self.state_detail.setText(message)

    def _estimate_remaining(self, done: int, total: int) -> str:
        now = monotonic()
        if not self.progress_started or not done or total != self.progress_total:
            self.progress_started = now
            self.progress_total = total
        remaining = (now - self.progress_started) * (total - done) / max(1, done)
        return f" · 约剩 {time_text(round(remaining))}" if done >= 2 and remaining >= 2 else ""

    @Slot()
    def _cancel(self) -> None:
        self._show_stage(Stage.CANCELLED)
        self.progress.hide()
        self.cancel.setEnabled(False)
        self.state_detail.setText("正在取消…")
        if self.phase == "preview":
            self.preview_cancel.set()
        else:
            self.job.cancel()

    def _can_replace(self) -> bool:
        return self.job.running or self.phase == "preview"

    @Slot(bool)
    def _on_finished(self, ok: bool) -> None:
        if self.close_pending:
            self._set_busy(False)
            self.close()
            return
        if self._start_pending():
            return
        if self.phase == "recognize" and ok and self.job_result is not None:
            result = self.job_result
            self.recognized = True
            targets = tuple(result["targets"])
            if self.model in ("demucs", "subroformer"):
                targets = tuple(track for track in targets if track in FOUR_STEM_TARGETS)
            self.targets = targets or (REMAINDER,)
            self.uncertain_targets = tuple(result.get("uncertain", ()))
            self._update_selection_button()
            self._set_busy(False)
            self._prepare_separation()
            return
        if self.phase == "separate" and ok and self.job_result:
            self.phase = "preview"
            self.preview_cancel.clear()
            self._show_stage(Stage.FINALIZING)
            self.state_detail.setText("正在生成波形与试听缓存…")
            self.paths = {name: Path(self.job_result["paths"][name]) for name in self.job_result["tracks"]}
            self.result_targets = self.targets
            self.result_quality = self.quality
            files = list(self.paths.values())
            cache = self.session.path / ".preview"
            BackgroundTask(lambda: load_preview(files, cache, check_cancel=self._check_preview_cancel)).start(
                self._preview_loaded
            )
            return
        self._set_busy(False)
        self._show_stage(Stage.CANCELLED if self.job.cancelled else Stage.FAILED)
        restored = self._restore_result()
        if not restored:
            self._discard_result()
            self._engine_status("本地处理" if self.job.cancelled else "处理失败", STATUS_IDLE if self.job.cancelled else ERROR)
            self._status("准备分离", "已取消，可以重新开始。" if self.job.cancelled else "可以重试分离。")
        if not self.job.cancelled:
            detail = self.job_error or self.job.stderr.decode("utf-8", errors="replace").strip()
            if "SHA-256 mismatch" in detail:
                self._model_error("本地模型校验失败", "请替换损坏的模型文件后重试。", detail, preserve_view=restored)
            else:
                self._show_notice(f"处理失败。{detail[-400:]}" if detail else "处理失败，请重试。")

    @Slot(object, str)
    def _preview_loaded(self, preview: Preview | None, error: str) -> None:
        self._set_busy(False)
        self.phase = ""
        if self.preview_cancel.is_set():
            if preview is not None:
                preview.close()
            if self.close_pending:
                self.close()
            elif not self._start_pending() and not self._restore_result():
                self._discard_result()
                self._engine_status("本地处理", STATUS_IDLE)
                self._status("准备分离", "已取消，可以重新开始。")
            return
        if preview is None and self._restore_result():
            self._show_stage(Stage.FAILED)
            self._show_notice(f"新音轨无法试听，已保留上一首歌曲。{error}")
            return
        if self.previous:
            self.results.clear()
            self.previous.cache.close()
            self.previous = None
        self._show_stage(Stage.COMPLETED)
        self.cancel.hide()
        self.primary.setText("保存音轨")
        self._engine_status("分离完成", STATUS_GREEN)
        if preview is None:
            self._status("分离完成", "可以保存音轨。")
            self._show_notice(f"无法准备试听。{error}")
            return
        self.results.set_preview(preview, tuple(self.paths))
        self.tracks.setEnabled(True)
        self.metadata.setText(f"{self.source.suffix[1:].upper()} · {time_text(round(preview.duration))}")
        self.content.setCurrentWidget(self.results)
        notice = ""
        if self.uncertain_targets:
            notice += f"「选择乐器」中有 {len(self.uncertain_targets)} 项待确认。"
        if tuple(self.paths) == (REMAINDER,):
            notice += "没有高置信度音轨。可以手动选择乐器后重新分离。"
        elif self.result_targets:
            notice += "实验性分离结果，可能有串音或误提取。"
        self._show_notice(notice, error=False)
        self.results.play_key.setFocus(Qt.FocusReason.OtherFocusReason)

    def _check_preview_cancel(self) -> None:
        if self.preview_cancel.is_set():
            raise InterruptedError("试听已取消")

    def _retain_result(self) -> None:
        if self.previous or not self.paths or self.session is None or self.source is None:
            return
        player = self.results.player
        playing = player is not None and player.playing
        if playing:
            self.results.toggle_play()
        setup = SongSetup(self.source, self.quality, self.model, self.targets)
        self.previous = PreviousResult(setup, self.session, self.paths, self.result_targets, self.result_quality,
                                       self.recognized, self.manual_targets, self.uncertain_targets,
                                       self.metadata.text(), self.primary.text(), self.notice.text(),
                                       self.notice.property("severity") == "error", playing)
        self.session, self.paths = None, {}

    def _clear_candidate(self) -> None:
        self.paths = {}
        if self.session:
            self.session.close()
            self.session = None

    def _restore_result(self) -> bool:
        previous = self.previous
        if previous is None:
            return False
        self.previous = None
        self._clear_candidate()
        self.source = previous.setup.source
        self.quality, self.model = previous.setup.quality, previous.setup.model
        self.targets = previous.setup.targets or ()
        self.result_targets, self.result_quality = previous.result_targets, previous.result_quality
        self.recognized, self.manual_targets = previous.selection_recognized, previous.manual
        self.uncertain_targets = previous.uncertain
        self.session, self.paths = previous.cache, previous.paths
        self._render_config()
        self._update_selection_button()
        self.song.setToolTip(self.source.name)
        self._fit_song_name()
        self.metadata.setText(previous.metadata)
        self.primary.setText(previous.primary_text)
        self._engine_status("分离完成", STATUS_GREEN)
        self._show_notice(previous.notice, error=previous.notice_error)
        self._set_busy(False)
        if self.results.player is not None:
            self.content.setCurrentWidget(self.results)
            if previous.resume:
                self.results.toggle_play()
        else:
            self._status("分离完成", "可以保存音轨。")
        return True

    def _start_pending(self) -> bool:
        if self.pending_setup is None:
            return False
        setup, self.pending_setup = self.pending_setup, None
        self._clear_candidate()
        self._set_busy(False)
        self._begin_song(setup)
        return True

    def _save(self) -> None:
        dialog = DeviceDialog(self, "保存音轨")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(4 * SPACE, 4 * SPACE, 4 * SPACE, 4 * SPACE)
        layout.setSpacing(2 * SPACE)
        layout.addWidget(QLabel("选择要保存的 WAV 音轨", objectName="dialogTitle"))
        tracks = {}
        scroll = QScrollArea(widgetResizable=True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumWidth(230)
        scroll.setFixedHeight(min(345, len(self.paths) * 34 + 10))
        options = QWidget()
        options_layout = QVBoxLayout(options)
        options_layout.setContentsMargins(4, 0, 4, 0)
        for name in self.paths:
            check = QCheckBox(LABELS[name], checked=True)
            tracks[name] = check
            options_layout.addWidget(check)
        options_layout.addStretch()
        scroll.setWidget(options)
        layout.addWidget(scroll)
        buttons = QHBoxLayout()
        cancel = HardwareButton("取消", width=85)
        choose = HardwareButton("选择文件夹", primary=True, width=125)
        cancel.clicked.connect(dialog.reject)
        choose.clicked.connect(dialog.accept)
        for check in tracks.values():
            check.toggled.connect(lambda: choose.setEnabled(any(item.isChecked() for item in tracks.values())))
        buttons.addWidget(cancel)
        buttons.addWidget(choose)
        layout.addLayout(buttons)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        selected = [self.paths[name] for name, check in tracks.items() if check.isChecked()] if accepted else []
        dialog.deleteLater()
        if not selected:
            return
        picker = DeviceFileDialog(self, "保存音轨到文件夹", str(self.source.parent))
        picker.setFileMode(QFileDialog.FileMode.Directory)
        picker.setOption(QFileDialog.Option.ShowDirsOnly)
        accepted = picker.exec() == QDialog.DialogCode.Accepted
        folder = picker.selectedFiles()[0] if accepted else ""
        picker.deleteLater()
        if not accepted or not folder:
            return
        root = Path(folder).resolve()
        if root.is_relative_to(self.cache_root.resolve()):
            self._show_notice("请选择临时音频缓存以外的文件夹。")
            return
        destination = root / self.source.stem
        suffix = 2
        while destination.exists() or destination.is_symlink():
            destination = root / f"{self.source.stem} ({suffix})"
            suffix += 1
        self._show_notice("正在保存音轨…", error=False)
        self.phase = "save"
        self._set_busy(True)
        BackgroundTask(lambda: copy_stems(destination, selected)).start(self._saved)

    @Slot(object, str)
    def _saved(self, destination: Path | None, error: str) -> None:
        self._set_busy(False)
        self._show_notice(f"无法保存音轨。{error}" if error else f"已保存到 {destination}", error=bool(error))
        if self.close_pending:
            self.close()

    def _discard_result(self) -> None:
        self.results.clear()
        self.tracks.setEnabled(False)
        self._clear_candidate()
        if self.previous:
            self.previous.cache.close()
            self.previous = None

    def _drag_source(self, event) -> Path | None:
        urls = event.mimeData().urls()
        if not self.choosing_song and (not self.busy or self._can_replace()) and len(urls) == 1 and urls[0].isLocalFile():
            path = Path(urls[0].toLocalFile())
            if path.suffix.lower() in FORMATS:
                return path
        return None

    def _highlight_drop(self, dragging: bool) -> None:
        self.dropzone.setProperty("dragging", dragging)
        self.dropzone.style().unpolish(self.dropzone)
        self.dropzone.style().polish(self.dropzone)

    def dragEnterEvent(self, event) -> None:
        if self._drag_source(event):
            self._highlight_drop(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._highlight_drop(False)
        event.accept()

    def dropEvent(self, event) -> None:
        self._highlight_drop(False)
        if source := self._drag_source(event):
            self.set_source(source)
            event.acceptProposedAction()

    def closeEvent(self, event) -> None:
        if self.busy:
            self.close_pending = True
            if self._can_replace():
                self._cancel()
            event.ignore()
            return
        self._discard_result()
        super().closeEvent(event)


def create_application() -> QApplication:
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Local Stem Separator")
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    translator = QTranslator(app)
    translations = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load("qtbase_zh_CN", translations):
        app.installTranslator(translator)
    app.setStyle(DeviceStyle("Fusion"))
    app.setPalette(silver_palette())
    app.setFont(interface_font())
    app.setWindowIcon(app_icon())
    app.setStyleSheet(STYLE)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="音轨分离")
    parser.add_argument("source", type=Path, nargs="?")
    parser.add_argument("--model-dir", type=Path)
    options = parser.parse_args(argv)
    app = create_application()
    local_cache = Path.cwd() / ".model-cache"
    app_cache = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "models"
    window = MainWindow(options.model_dir or (local_cache if local_cache.is_dir() else app_cache))
    window.show()
    if options.source:
        QTimer.singleShot(0, lambda: window.set_source(options.source))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
