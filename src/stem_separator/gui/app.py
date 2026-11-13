import argparse
from pathlib import Path
import sys
from tempfile import gettempdir

from PySide6.QtCore import QStandardPaths, Qt, QTimer, Slot
from PySide6.QtGui import QBrush, QFont, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QProgressBar, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)
import soundfile as sf

from stem_separator.audio.playback import Preview, load_preview
from stem_separator.application.instruments import EFFECT_PROMPTS, HEAD_IDS, LABELS, REMAINDER, STANDARD
from stem_separator.gui.audio_cache import AudioCache
from stem_separator.gui.controls import StatusLamp, time_text
from stem_separator.gui.hardware import HardwareButton
from stem_separator.gui.instrument_menu import InstrumentMenu
from stem_separator.gui.jobs import BackgroundTask, SeparationJob
from stem_separator.gui.results import Results
from stem_separator.gui.theme import STYLE, app_icon, matte_tile, silver_palette, technical_font
from stem_separator.storage.export import copy_stems
from stem_separator.storage.model_cache import assets_for_targets

FORMATS = (".wav", ".mp3", ".flac", ".m4a", ".ogg")


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
        self.model_needs_repair: set[str] = set()
        self.targets: tuple[str, ...] = ()
        self.result_targets: tuple[str, ...] = ()
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
        self.notice = QLabel(objectName="error", wordWrap=True)
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
        for widget in (icon, heading, pick, QLabel("支持 WAV、MP3、FLAC", objectName="muted")):
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
        row = QHBoxLayout(header)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)
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
        self.engine.setMinimumWidth(57)
        self.engine.setAccessibleName("处理状态")
        row.addWidget(self.lamp)
        row.addWidget(self.engine)
        row.addSpacing(4)
        self.change = HardwareButton("换首歌", width=93)
        self.change.clicked.connect(self.choose_file)
        self.instruments = HardwareButton("选择乐器  ⌄", width=123)
        self.instruments.clicked.connect(self._choose_instruments)
        self.primary = HardwareButton("开始分离", primary=True, width=132)
        self.primary.clicked.connect(self._primary_action)
        row.addWidget(self.change)
        row.addWidget(self.instruments)
        row.addWidget(self.primary)
        layout.addWidget(header)

        self.content = QStackedWidget()
        panel = QFrame(objectName="well")
        state = QVBoxLayout(panel)
        state.setContentsMargins(24, 20, 24, 20)
        state.setSpacing(13)
        state.addStretch(1)
        self.state_title = QLabel("准备分离", objectName="stateTitle", alignment=Qt.AlignmentFlag.AlignCenter)
        self.state_detail = QLabel("人声、鼓、贝斯和其他", objectName="muted",
                                   alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.progress = QProgressBar(textVisible=False)
        self.progress.setFixedWidth(310)
        self.progress.setAccessibleName("分离进度")
        self.cancel = HardwareButton("取消", width=88)
        self.cancel.clicked.connect(self._cancel)
        state.addWidget(self.state_title)
        state.addWidget(self.state_detail)
        state.addWidget(self.progress, alignment=Qt.AlignmentFlag.AlignHCenter)
        state.addWidget(self.cancel, alignment=Qt.AlignmentFlag.AlignHCenter)
        state.addStretch(1)
        self.results = Results()
        self.results.audio_error.connect(lambda detail: self._show_notice(f"无法播放，请检查系统音频输出。{detail}"))
        self.content.addWidget(panel)
        self.content.addWidget(self.results)
        layout.addWidget(self.content, 1)
        self._status("准备分离", "人声、鼓、贝斯和其他")
        return workspace

    def _status(self, title: str, detail: str, *, progress: bool = False, cancellable: bool = False) -> None:
        self.content.setCurrentIndex(0)
        self.state_title.setText(title)
        self.state_detail.setText(detail)
        self.progress.setVisible(progress)
        self.cancel.setVisible(cancellable)
        self.cancel.setEnabled(cancellable)

    def _show_notice(self, message: str = "", *, error: bool = True) -> None:
        self.notice.setStyleSheet("color: #202020; font-weight: 600;" if error else "color: #333333;")
        self.notice.setText(message)
        self.notice.setVisible(bool(message))

    def _engine_status(self, label: str, color: str) -> None:
        self.engine.setText(label)
        self.lamp.set_color(color)

    @Slot()
    def choose_file(self) -> None:
        if self.busy:
            return
        start = str(self.source.parent) if self.source else ""
        file, _ = QFileDialog.getOpenFileName(self, "选择歌曲", start,
                                              "音频文件 (*.wav *.mp3 *.flac *.m4a *.ogg)")
        if file:
            self.set_source(Path(file))

    def set_source(self, source: Path) -> None:
        if self.busy:
            return
        if not source.is_file() or source.suffix.lower() not in FORMATS:
            self._show_notice("请选择一个本地 WAV、MP3、FLAC、M4A 或 Ogg 音频文件。")
            return
        file_type = source.suffix[1:].upper()
        try:
            duration = sf.info(source).duration
        except (OSError, RuntimeError):
            metadata = file_type
        else:
            metadata = f"{file_type} · {time_text(round(duration))}"
        self._discard_result()
        self._show_notice()
        self.source = source.resolve()
        self.song.setToolTip(self.source.name)
        self.pages.setCurrentWidget(self.workspace)
        self._fit_song_name()
        self.metadata.setText(metadata)
        self.primary.setText("开始分离")
        self._engine_status("本地处理", "#758178")
        self._status("准备分离", self._selection_detail())

    def _fit_song_name(self) -> None:
        if self.source:
            width = max(120, self.song.width())
            self.song.setText(self.song.fontMetrics().elidedText(self.source.name, Qt.TextElideMode.ElideMiddle, width))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_song_name()

    @Slot()
    def _primary_action(self) -> None:
        if self.busy or self.source is None:
            return
        if self.paths and self.targets == self.result_targets:
            self._save()
        elif any(not asset.path.is_file() or asset.path.name in self.model_needs_repair
                 for asset in assets_for_targets(self.model_dir, self.targets)):
            self._ask_to_download()
        else:
            self.start_separation()

    @Slot()
    def _choose_instruments(self) -> None:
        menu = InstrumentMenu(self.instruments, self.targets)
        menu.applied.connect(self._apply_instruments)
        menu.open()

    @Slot(tuple)
    def _apply_instruments(self, targets: tuple[str, ...]) -> None:
        self.targets = targets
        self.instruments.setText(f"乐器 {len(targets)} 项  ⌄" if targets else "选择乐器  ⌄")
        self.instruments.setToolTip(self._selection_detail())
        self.primary.setText("保存音轨" if self.paths and targets == self.result_targets else "开始分离")
        if not self.paths:
            self._status("准备分离", self._selection_detail())

    def _selection_detail(self) -> str:
        if not self.targets:
            return "人声、鼓、贝斯和其他"
        return "实验性 · " + "、".join(LABELS[name] for name in self.targets)

    def _ask_to_download(self) -> None:
        dialog = QDialog(self, windowTitle="准备模型")
        layout = QVBoxLayout(dialog)
        assets = assets_for_targets(self.model_dir, self.targets)
        repair = any(asset.path.name in self.model_needs_repair for asset in assets)
        instrument = any(name in HEAD_IDS for name in self.targets)
        effect = any(name in EFFECT_PROMPTS for name in self.targets)
        size = "2.2 GB" if instrument and effect else "1.27 GB" if instrument else "0.9 GB" if effect else "80 MB"
        message = "模型文件损坏，需要重新下载。" if repair else f"首次使用这组音轨需准备模型，最多下载约 {size}。"
        layout.addWidget(QLabel(f"{message}\n下载完成后可离线使用。"))
        buttons = QHBoxLayout()
        later, download = HardwareButton("稍后"), HardwareButton("下载模型", primary=True)
        later.clicked.connect(dialog.reject)
        download.clicked.connect(dialog.accept)
        buttons.addWidget(later)
        buttons.addWidget(download)
        layout.addLayout(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._start_job("setup", *self.targets)

    def start_separation(self) -> None:
        if self.busy or self.source is None:
            return
        self._discard_result()
        try:
            self.session = AudioCache(self.cache_root)
        except OSError as error:
            self._show_notice(f"无法创建临时目录。{error}")
            return
        self._start_job("separate", self.source, self.session.path, *self.targets)

    def _start_job(self, phase: str, *paths: Path | str) -> None:
        self.phase = phase
        self.job_error = ""
        self.job_result = None
        self._show_notice()
        self._set_busy(True)
        self._engine_status("准备模型" if phase == "setup" else "正在分离", "#b4503c")
        self.progress.setRange(0, 0)
        if phase == "setup":
            self._status("正在下载模型", "下载完成后会开始分离。", progress=True, cancellable=True)
        else:
            self._status("正在分离", "正在启动本地处理…", progress=True, cancellable=True)
        self.job.start(phase, self.model_dir, *paths)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.primary.setEnabled(not busy)
        self.change.setEnabled(not busy)
        self.instruments.setEnabled(not busy)

    @Slot(dict)
    def _on_event(self, event: dict) -> None:
        kind = event["event"]
        if kind == "error":
            self.job_error = event["message"]
        elif kind == "result":
            self.job_result = event
        elif kind == "progress" and not self.job.cancelled:
            stage = event["stage"]
            detail = {"decode": "正在读取音频…", "load": "正在加载本地模型…", "export": "正在准备试听音轨…"}
            if stage == "inference":
                done, total = event["completed"], event["total"]
                self.progress.setRange(0, total)
                self.progress.setValue(done)
                device = "GPU" if event["message"].startswith("CUDA") else "CPU"
                self.state_detail.setText(f"{device} 处理中 · {round(100 * done / total)}%")
            elif stage == "download":
                total = event["total"]
                self.progress.setRange(0, 100 if total else 0)
                if total:
                    percent = round(100 * event["completed"] / total)
                    self.progress.setValue(percent)
                    self.state_detail.setText(f"下载模型 · {percent}%")
                else:
                    self.state_detail.setText("正在准备模型文件…")
            elif stage == "device":
                self.progress.setRange(0, 0)
                self.state_detail.setText("显存不足，正在调整处理方式…")
            else:
                self.state_detail.setText(detail[stage])

    @Slot()
    def _cancel(self) -> None:
        self.cancel.setEnabled(False)
        self.state_detail.setText("正在取消…")
        self.job.cancel()

    @Slot(bool)
    def _on_finished(self, ok: bool) -> None:
        if self.close_pending:
            self._set_busy(False)
            self.close()
            return
        if self.phase == "setup" and ok:
            self.model_needs_repair.difference_update(asset.path.name for asset in assets_for_targets(self.model_dir, self.targets))
            self._set_busy(False)
            self.start_separation()
            return
        if self.phase == "separate" and ok and self.job_result:
            names = (*self.targets, REMAINDER) if self.targets else STANDARD
            self.paths = {name: Path(self.job_result["paths"][name]) for name in names}
            self.result_targets = self.targets
            self.cancel.hide()
            files = list(self.paths.values())
            cache = self.session.path / ".preview"
            BackgroundTask(lambda: load_preview(files, cache)).start(self._preview_loaded)
            return
        self._set_busy(False)
        self._discard_result()
        self._engine_status("本地处理" if self.job.cancelled else "处理失败", "#758178" if self.job.cancelled else "#a14b3b")
        self._status("准备分离", "已取消，可以重新开始。" if self.job.cancelled else "可以重试分离。")
        if not self.job.cancelled:
            detail = self.job_error or self.job.stderr.decode("utf-8", errors="replace").strip()
            if "SHA-256 mismatch" in detail:
                self.model_needs_repair.update(asset.path.name for asset in assets_for_targets(self.model_dir, self.targets)
                                               if asset.path.name in detail)
                self._show_notice("模型文件校验失败。点击「开始分离」可重新下载。")
            else:
                self._show_notice(f"处理失败。{detail[-400:]}" if detail else "处理失败，请重试。")

    @Slot(object, str)
    def _preview_loaded(self, preview: Preview | None, error: str) -> None:
        self._set_busy(False)
        if self.close_pending:
            if preview is not None:
                preview.close()
            self.close()
            return
        self.primary.setText("保存音轨")
        self._engine_status("分离完成", "#61836d")
        if preview is None:
            self._status("分离完成", "可以保存音轨。")
            self._show_notice(f"无法准备试听。{error}")
            return
        self.results.set_preview(preview, tuple(self.paths))
        self.metadata.setText(f"{self.source.suffix[1:].upper()} · {time_text(round(preview.duration))}")
        self.content.setCurrentWidget(self.results)
        if self.result_targets:
            self._show_notice("实验性分离结果，可能有串音或误提取。", error=False)
        self.results.play_key.setFocus(Qt.FocusReason.OtherFocusReason)

    def _save(self) -> None:
        dialog = QDialog(self, windowTitle="保存音轨")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addWidget(QLabel("选择要保存的 WAV 音轨"))
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
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        folder = QFileDialog.getExistingDirectory(self, "保存音轨到文件夹", str(self.source.parent))
        if not folder:
            return
        root = Path(folder).resolve()
        if root.is_relative_to(self.cache_root.resolve()):
            self._show_notice("请选择临时音频缓存以外的文件夹。")
            return
        selected = [self.paths[name] for name, check in tracks.items() if check.isChecked()]
        destination = root / self.source.stem
        suffix = 2
        while destination.exists() or destination.is_symlink():
            destination = root / f"{self.source.stem} ({suffix})"
            suffix += 1
        self._show_notice("正在保存音轨…", error=False)
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
        self.paths = {}
        if self.session:
            self.session.close()
            self.session = None

    def _drag_source(self, event) -> Path | None:
        urls = event.mimeData().urls()
        if not self.busy and len(urls) == 1 and urls[0].isLocalFile():
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
            if self.job.running:
                self._cancel()
            event.ignore()
            return
        self._discard_result()
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="音轨分离")
    parser.add_argument("source", type=Path, nargs="?")
    parser.add_argument("--model-dir", type=Path)
    options = parser.parse_args(argv)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Local Stem Separator")
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyle("Fusion")
    app.setPalette(silver_palette())
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setWindowIcon(app_icon())
    app.setStyleSheet(STYLE)
    local_cache = Path.cwd() / ".model-cache"
    app_cache = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "models"
    window = MainWindow(options.model_dir or (local_cache if local_cache.is_dir() else app_cache))
    window.show()
    if options.source:
        QTimer.singleShot(0, lambda: window.set_source(options.source))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
