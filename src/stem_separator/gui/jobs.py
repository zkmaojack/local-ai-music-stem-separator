from collections.abc import Callable
import json
from pathlib import Path
import sys

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QRunnable, QTemporaryDir, QThreadPool, QTimer, Signal

class SeparationJob(QObject):
    event = Signal(dict)
    finished = Signal(bool)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(self._read_error)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.kill_timer = QTimer(self, singleShot=True, interval=5000)
        self.kill_timer.timeout.connect(self.process.kill)
        self.buffer = b""
        self.stderr = b""
        self.cancelled = False
        self.cancel_dir: QTemporaryDir | None = None

    @property
    def running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    def start(self, action: str, model_dir: Path, *arguments: Path | str) -> None:
        self.buffer = self.stderr = b""
        self.cancelled = False
        python = Path(sys.executable)
        if python.name.lower() == "pythonw.exe":
            python = python.with_name("python.exe")
        self.cancel_dir = QTemporaryDir()
        environment = QProcessEnvironment.systemEnvironment()
        environment.remove("STEM_SEPARATOR_CANCEL_FILE")
        if self.cancel_dir.isValid():
            environment.insert("STEM_SEPARATOR_CANCEL_FILE", self.cancel_dir.filePath("cancel"))
        self.process.setProcessEnvironment(environment)
        command = ["-m", "stem_separator.gui.worker", action, str(model_dir), *map(str, arguments)]
        self.process.start(str(python), command)

    def cancel(self) -> None:
        if self.running:
            self.cancelled = True
            if self.cancel_dir is not None and self.cancel_dir.isValid():
                try:
                    Path(self.cancel_dir.filePath("cancel")).touch()
                except OSError:
                    self.process.kill()
                    return
            self.kill_timer.start()

    def _read(self) -> None:
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                self.event.emit(json.loads(line))
            except (UnicodeError, json.JSONDecodeError):
                self.stderr = (self.stderr + line + b"\n")[-8192:]

    def _read_error(self) -> None:
        self.stderr = (self.stderr + bytes(self.process.readAllStandardError()))[-8192:]

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.cancel_dir = None
            self.event.emit({"event": "error", "message": self.process.errorString()})
            self.finished.emit(False)

    def _finished(self, code: int, status: QProcess.ExitStatus) -> None:
        self.kill_timer.stop()
        self.cancel_dir = None
        self._read()
        self._read_error()
        ok = code == 0 and status == QProcess.ExitStatus.NormalExit and not self.cancelled
        self.finished.emit(ok)


class TaskSignals(QObject):
    done = Signal(object, str)


class BackgroundTask(QRunnable):
    def __init__(self, work: Callable[[], object]) -> None:
        super().__init__()
        self.work = work
        self.signals = TaskSignals()

    def run(self) -> None:
        try:
            self.signals.done.emit(self.work(), "")
        except Exception as error:
            self.signals.done.emit(None, str(error))

    def start(self, callback: Callable[[object, str], None]) -> None:
        self.signals.done.connect(callback)
        QThreadPool.globalInstance().start(self)
