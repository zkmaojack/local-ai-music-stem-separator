"""Exercise the real Qt automatic import, playback and export path on Windows."""
import argparse
import json
from pathlib import Path
from time import perf_counter

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog

from stem_separator.gui.app import MainWindow, create_application
from stem_separator.gui.song_setup import SongSetupDialog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--quality", choices=("fast", "balanced", "high"), default="fast")
    args = parser.parse_args()
    app = create_application()
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow(Path(".model-cache"))
    window.show()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    last_events = {}
    window.job.event.connect(lambda event: last_events.update({window.phase: event}) if event["event"] == "result" else None)
    heartbeat = []
    started = perf_counter()
    timer = QTimer(interval=100)
    timer.timeout.connect(lambda: heartbeat.append(perf_counter()))
    stages = set()
    stage = "import"

    def poll():
        nonlocal stage
        try:
            if window.busy:
                key = (window.phase, window.processing_stage.name)
                if key not in stages:
                    stages.add(key)
                    window.grab().save(str(folder / ('-'.join(key) + '.png')))
                return
            if stage == "import":
                if window.results.player is None:
                    raise AssertionError(window.job_error or window.state_detail.text() or window.notice.text())
                assert set(last_events) == {"recognize", "separate"}
                assert last_events["separate"]["inference_seconds"] > 0
                assert any(track not in ("other", "remainder") for track in window.paths)
                window.grab().save(str(folder / "workspace.png"))
                window.results.seek(.2)
                window.results.toggle_play()
                assert window.results.player.playing, window.notice.text()
                stage = "playback"
            elif stage == "playback":
                assert window.results.player.position / window.results.player.preview.frames >= .2
                window.results.toggle_play()
                key = next(button for button in window.results.solos if button.isVisible())
                key.click()
                assert window.results.active_solo
                key.click()
                # The system directory chooser is substituted so the export stays in QA.
                QFileDialog.getExistingDirectory = lambda *_: str(folder)
                QTimer.singleShot(120, lambda: QApplication.activeModalWidget().accept())
                stage = "export"
                window.primary.click()
            else:
                assert window.notice.text().startswith("已保存到"), window.notice.text()
                assert list(folder.glob("*/*.wav"))
                report = {"platform": app.platformName(), "quality": args.quality, "elapsed_seconds": perf_counter() - started,
                          "longest_heartbeat_gap": max(b - a for a, b in zip(heartbeat, heartbeat[1:])),
                          "stages": sorted(stages), "events": last_events, "archive_notice": window.notice.text()}
                (folder / "gui-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                timer.stop()
                poll_timer.stop()
                window.close()
                app.quit()
        except Exception:
            import traceback
            traceback.print_exc()
            window.close()
            app.exit(1)

    def accept_setup():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, SongSetupDialog)
        dialog.quality.setCurrentIndex(dialog.quality.findData(args.quality))
        assert dialog.confirm.isEnabled(), dialog.detail.text()
        dialog.confirm.click()

    def start():
        QTimer.singleShot(150, accept_setup)
        window.set_source(args.source)
        timer.start()
        poll_timer.start()

    poll_timer = QTimer(interval=300)
    poll_timer.timeout.connect(poll)
    QTimer.singleShot(200, start)
    QTimer.singleShot(600_000, lambda: app.exit(2))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
