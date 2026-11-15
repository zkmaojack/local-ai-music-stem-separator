"""Run real local Demucs jobs and record GUI overhead separately from inference.

Outputs and a short input excerpt stay in the ignored build directory.
Run with: python tools/benchmark_ui.py before (or after).
"""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import time

import soundfile as sf
from PySide6.QtCore import QEvent, QObject, QTimer

from stem_separator.gui.app import MainWindow, create_application
from stem_separator.gui.processing import Stage
from stem_separator.gui.song_setup import SongSetup


class Memory(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in
        ("peak_rss", "rss", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]


def rss_mb():
    counters = Memory()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
    ctypes.windll.psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return counters.rss / 1024 ** 2


class Probe(QObject):
    paints = 0

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Paint:
            self.paints += 1
        return False


root = Path(__file__).resolve().parents[1]
output = root / "build" / "ui-qa" / sys.argv[1]
output.mkdir(parents=True, exist_ok=True)
source = root / "koi is love.flac"
audio, rate = sf.read(source, frames=8 * 44100, dtype="float32")
short = output / "short.wav"
sf.write(short, audio, rate)
app = create_application()
window = MainWindow(root / ".model-cache")
window.resize(1200, 780)
window.show()
probe = Probe()
app.installEventFilter(probe)
reports = []
queue = [short, source]
job_started = cpu_started = 0.
paint_started = 0
peak_rss = 0.
last_poll = time.perf_counter()
max_gap = 0.
captured = set()
static_started = False


def begin():
    global job_started, cpu_started, paint_started, peak_rss, max_gap, last_poll
    if not queue:
        static_sample()
        return
    path = queue.pop(0)
    captured.clear()
    job_started, cpu_started = time.perf_counter(), time.process_time()
    last_poll = job_started
    paint_started, peak_rss, max_gap = probe.paints, rss_mb(), 0.
    window._begin_song(SongSetup(path, "fast", "demucs", ()))


def poll():
    global peak_rss, last_poll, max_gap
    now = time.perf_counter()
    max_gap = max(max_gap, now - last_poll)
    last_poll = now
    peak_rss = max(peak_rss, rss_mb())
    if window.busy:
        stage = window.processing_stage.name
        if stage not in captured:
            window.grab().save(str(output / f"{window.source.stem}-{stage}.png"))
            captured.add(stage)
        fraction = getattr(window.progress, "fraction", None)
        if fraction is not None and 0 < fraction < 1 and "progress" not in captured:
            window.grab().save(str(output / f"{window.source.stem}-progress.png"))
            captured.add("progress")
    elif job_started and not static_started:
        if window.job_result is None or window.job_error:
            print(window.job_error or window.job.stderr.decode(errors="replace"), flush=True)
            app.exit(1)
            return
        elapsed = now - job_started
        reports.append({"source": window.source.name, "wall_seconds": elapsed,
                        "gui_cpu_seconds": time.process_time() - cpu_started,
                        "gui_paints": probe.paints - paint_started, "gui_peak_rss_mb": peak_rss,
                        "max_poll_gap_seconds": max_gap, "worker": window.job_result})
        window.grab().save(str(output / f"{window.source.stem}-complete.png"))
        print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
        begin()


def static_sample():
    global static_started
    static_started = True
    timer.stop()
    window._status("正在分离音轨", "正在处理音频片段", cancellable=True)
    window._show_stage(Stage.SEPARATING)
    QTimer.singleShot(500, start_idle)


def start_idle():
    start, cpu, paints = time.perf_counter(), time.process_time(), probe.paints

    def done():
        reports.append({"static_seconds": time.perf_counter() - start,
                        "gui_cpu_seconds": time.process_time() - cpu,
                        "gui_paints": probe.paints - paints, "gui_rss_mb": rss_mb()})
        (output / "metrics.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(reports[-1]), flush=True)
        window.close()
        app.quit()

    QTimer.singleShot(8000, done)


timer = QTimer(interval=100)
timer.timeout.connect(poll)
QTimer.singleShot(300, begin)
QTimer.singleShot(500, timer.start)
QTimer.singleShot(600_000, lambda: app.exit(2))
raise SystemExit(app.exec())
