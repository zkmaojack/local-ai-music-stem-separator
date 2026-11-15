from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from stem_separator.application import recognition
from stem_separator.application.recognition import classify_yamnet, non_overlapping_targets
from stem_separator.storage.model_cache import assets_for_targets


def test_recognition_keeps_a_family_when_specific_evidence_is_weak() -> None:
    patches = np.zeros((12, 521), dtype=np.float32)
    patches[:, 185] = .42
    patches[0, 186] = .68
    patches[2:5, 191] = .63
    assert non_overlapping_targets(classify_yamnet(patches)) == ("flute", "strings")

    patches[1:4, 186] = .72
    patches[6:9, 188] = .66
    assert non_overlapping_targets(classify_yamnet(patches)) == ("cello", "flute", "violin")


def test_clap_reviews_a_partial_yamnet_result(tmp_path, monkeypatch) -> None:
    interpreter = pytest.importorskip("ai_edge_litert.interpreter")
    checkpoint = tmp_path / "model"
    checkpoint.touch()
    asset = SimpleNamespace(path=checkpoint, open_checkpoint=lambda: BytesIO())

    class StubInterpreter:
        def __init__(self, **_kwargs):
            pass

        def allocate_tensors(self):
            pass

        def get_input_details(self):
            return [{"index": 0, "shape": (16000,)}]

        def get_output_details(self):
            return [{"index": 1}]

        def set_tensor(self, _index, _array):
            pass

        def invoke(self):
            pass

        def get_tensor(self, _index):
            values = np.zeros((1, 521), dtype=np.float32)
            values[0, 24] = .8
            return values

    def refine(_audio, yamnet, _families, _weak, _assets, _cancel, _progress):
        assert yamnet == {"vocal": pytest.approx(.8)}
        return {"flute": .75}, ("oboe",), ()

    monkeypatch.setattr(interpreter, "Interpreter", StubInterpreter)
    monkeypatch.setattr(recognition, "recognition_assets", lambda *_: (asset, asset))
    monkeypatch.setattr(recognition, "decode_audio", lambda *_, **_args: np.ones((1, 16000), dtype=np.float32))
    monkeypatch.setattr(recognition, "refine_clap", refine)

    result = recognition.recognize(checkpoint, tmp_path, "balanced", lambda _: None, lambda: None)
    assert set(result.tracks) == {"vocal", "flute"}
    assert result.uncertain == ("oboe",)


def test_quality_downloads_match_models(tmp_path: Path) -> None:
    assert len(assets_for_targets(tmp_path, ("vocal",), "fast")) == 1
    assert len(assets_for_targets(tmp_path, ("vocal",), "balanced")) == 2
    assert len(assets_for_targets(tmp_path, ("vocal",), "high")) == 4
    assert not assets_for_targets(tmp_path, ("remainder",), "balanced")


def test_explicit_models_share_the_same_routing_for_checks_and_workers(tmp_path: Path) -> None:
    from stem_separator.application.instruments import validate_model_selection
    from stem_separator.application.types import SeparationError
    from stem_separator.backends.selection import build_backend

    for model, quality, targets, name, cache in (
        ("demucs", "high", ("drums",), "htdemucs", tmp_path.name),
        ("subroformer", "fast", ("drums",), "SubRoformer", "subroformer"),
        ("detailed", "balanced", ("drums",), "MVSep Mega 53", "mega53"),
        ("detailed", "balanced", ("impact",), "AudioSep", "audiosep"),
    ):
        assert {asset.path.parent.name for asset in assets_for_targets(tmp_path, targets, quality, model)} == {cache}
        assert build_backend(tmp_path, targets, quality, model).spec.name == name
    with pytest.raises(SeparationError):
        validate_model_selection(("violin",), "demucs")


def test_import_starts_separation_and_visibility_does_not_change_mix(tmp_path, monkeypatch) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QCheckBox
    from stem_separator.audio.playback import load_preview
    from stem_separator.gui import app as gui

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(gui, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(gui, "assets_for_targets", lambda *args: ())
    (tmp_path / "subroformer").mkdir()
    for asset in assets_for_targets(tmp_path, (), "balanced", "auto"):
        asset.path.touch()
    application = QApplication.instance() or QApplication([])
    track = tmp_path / "track.wav"
    sf.write(track, np.zeros((441, 2), dtype=np.float32), 44100)
    window = gui.MainWindow(tmp_path)
    starts = []
    monkeypatch.setattr(window.job, "start", lambda *args, **kwargs: starts.append(args))
    try:
        window._begin_song(gui.SongSetup(track, "balanced", "auto", None))
        assert starts[-1] == ("recognize", tmp_path, "balanced", track)
        window.job_result = {"targets": ["vocal", "drums"]}
        window._on_finished(True)
        assert starts[-1][0] == "separate"
        assert starts[-1][-2:] == ("automatic", "auto")

        window.paths = {"vocal": track, "drums": track}
        window.result_targets = window.targets
        window.result_quality = window.quality
        window._preview_loaded(load_preview([track, track], tmp_path / "preview"), "")
        menu = window.results.visibility_menu(window.tracks)
        checks = {check.text(): check for check in menu.findChildren(QCheckBox)}
        before = window.results.player.gains
        checks["人声"].setChecked(False)
        assert window.results.rows["vocal"][1][0].isHidden()
        assert window.results.player.gains == before
        assert tuple(window.paths) == ("vocal", "drums")
        checks["人声"].setChecked(True)
        assert not window.results.rows["vocal"][1][0].isHidden()
        application.processEvents()
    finally:
        window._set_busy(False)
        window.close()
