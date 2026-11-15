from dataclasses import dataclass
import numpy as np
import pytest
import soundfile as sf
from types import SimpleNamespace

from stem_separator.application.planning import plan_automatic
from stem_separator.application import recognition
from stem_separator.application.recognition import recognize
from stem_separator.application.separation import SeparationService
from stem_separator.application.stem_validation import quiet_unconfirmed_stems
from stem_separator.application.types import SeparationError, SeparationRequest
from stem_separator.backends.base import InferenceResult, ModelSpec
from stem_separator.storage.model_cache import assets_for_targets


def install_placeholder(root, model, quality):
    for asset in assets_for_targets(root, (), quality, model):
        asset.path.parent.mkdir(parents=True, exist_ok=True)
        asset.path.touch()


@pytest.mark.parametrize("quality,model,candidates,unresolved", [
    ("fast", "auto", (), ()),
    ("balanced", "auto", ("synth",), ("synth",)),
    ("balanced", "demucs", ("synth",), ("synth",)),
    ("balanced", "demucs", ("drums", "synth", "kick"), ("synth", "kick")),
])
def test_automatic_routes_always_attempt_four_stems(tmp_path, quality, model, candidates, unresolved):
    install_placeholder(tmp_path, model, quality)
    plan = plan_automatic(tmp_path, quality, model, candidates)
    assert plan.model == model
    assert plan.targets == ()
    assert plan.recognized == candidates
    assert plan.unresolved == unresolved


def test_auto_falls_back_to_cached_demucs_but_fixed_model_does_not(tmp_path):
    install_placeholder(tmp_path, "demucs", "fast")
    assert plan_automatic(tmp_path, "balanced", "auto", ()).model == "demucs"
    with pytest.raises(SeparationError, match="缺少"):
        plan_automatic(tmp_path, "balanced", "subroformer", ())


def test_automatic_retry_chooses_a_different_installed_model(tmp_path):
    install_placeholder(tmp_path, "auto", "balanced")
    install_placeholder(tmp_path, "demucs", "fast")
    plan = plan_automatic(tmp_path, "balanced", "auto", ("synth",), attempted=(("auto", "balanced"),))
    assert (plan.model, plan.targets, plan.unresolved) == ("demucs", (), ("synth",))


def test_detailed_route_uses_supported_targets_with_available_weights(tmp_path):
    for asset in assets_for_targets(tmp_path, ("synth",), "balanced", "detailed"):
        asset.path.parent.mkdir(parents=True, exist_ok=True)
        asset.path.touch()
    plan = plan_automatic(tmp_path, "balanced", "detailed", ("synth", "riser"))
    assert (plan.model, plan.targets, plan.unresolved) == ("detailed", ("synth",), ("riser",))


def test_silent_invalid_and_recognizer_failure_are_distinct(tmp_path):
    def noop(*_):
        pass

    rate = 16000
    silence = tmp_path / "silent.wav"
    sf.write(silence, np.zeros((rate, 2)), rate)
    assert not recognize(silence, tmp_path, "fast", noop, noop).has_audio
    signal = tmp_path / "audio.wav"
    sf.write(signal, np.full((rate, 2), .02), rate)
    missing = recognize(signal, tmp_path, "fast", noop, noop)
    assert missing.has_audio and missing.warning and not missing.tracks
    invalid = tmp_path / "bad.wav"
    invalid.write_bytes(b"invalid file")
    with pytest.raises(SeparationError, match="FFmpeg"):
        recognize(invalid, tmp_path, "fast", noop, noop)


def test_optional_passt_adds_candidates_without_overriding_validated_labels(tmp_path, monkeypatch):
    model = tmp_path / "model.pt"
    model.touch()
    monkeypatch.setattr(recognition, "decode_audio", lambda *a, **k: np.ones((2, 16000), dtype=np.float32))
    monkeypatch.setattr(recognition, "passt_asset", lambda _: SimpleNamespace(path=model))
    monkeypatch.setattr(recognition, "recognize_audio", lambda *a: recognition.RecognitionResult(("drums",), ("piano",)))
    monkeypatch.setattr(recognition, "recognize_passt", lambda *a: recognition.RecognitionResult(("synth",), ("violin",)))
    result = recognize(tmp_path / "song.wav", tmp_path, "high", lambda _: None, lambda: None, use_passt=True)
    assert result.tracks == ("drums",)
    assert result.uncertain == ("piano", "synth", "violin")


@dataclass
class Backend:
    failure: bool = False
    spec = ModelSpec("test model", 44100, 2, ("vocals", "drums", "bass", "other"))

    def load(self):
        pass

    def unload(self):
        pass

    def separate(self, audio, device, progress):
        if self.failure:
            raise SeparationError("inference failed")
        quiet = audio * .0001
        return InferenceResult({"vocals": np.zeros_like(audio), "bass": np.zeros_like(audio),
                                "drums": quiet, "other": audio - quiet}, "CPU", 0.)


def test_post_validation_keeps_quiet_sources_and_does_not_publish_failures(tmp_path):
    source = tmp_path / "test.wav"
    sf.write(source, np.full((4410, 2), .08), 44100)
    request = SeparationRequest(source, tmp_path / "out")
    result = SeparationService(Backend()).run(request, lambda _: None, filter_empty=True)
    assert tuple(result.paths) == ("drums", "other")
    assert result.diagnostics["vocals"]["silent_block_ratio"] == 1
    assert result.diagnostics["drums"]["peak"] < .00001
    assert quiet_unconfirmed_stems(result.diagnostics, ("drums",)) == ("vocals", "bass")
    assert quiet_unconfirmed_stems(result.diagnostics, ()) == ("vocals", "drums", "bass")
    exported = sum((sf.read(path, dtype="float64")[0] for path in result.paths.values()))
    np.testing.assert_allclose(exported, sf.read(source)[0], atol=3e-7)
    bad = SeparationRequest(source, tmp_path / "failed")
    with pytest.raises(SeparationError, match="inference failed"):
        SeparationService(Backend(failure=True)).run(bad, lambda _: None, filter_empty=True)
    assert not (bad.output_root / source.stem).exists()
