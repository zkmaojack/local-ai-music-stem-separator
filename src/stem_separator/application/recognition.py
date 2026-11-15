from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from stem_separator.application.instruments import GROUPS, HEAD_IDS, INSTRUMENTS
from stem_separator.application.types import Progress, ProgressCallback, Quality, SeparationError
from stem_separator.audio.decode import decode_audio, has_signal
from stem_separator.storage.model_cache import ModelCache, passt_asset, recognition_assets

# Indices in the official YAMNet AudioSet class map, with per-track score thresholds.
TAG_CLASSES = {
    "vocal": ((24, 25, 31), .33), "guitar": ((135,), .42), "electric-guitar": ((136,), .37),
    "bass": ((137,), .23), "acoustic-guitar": ((138,), .42), "banjo": ((142,), .42),
    "sitar": ((143,), .42), "mandolin": ((144,), .42), "ukulele": ((146,), .42),
    "keys": ((147,), .44), "piano": ((148,), .38), "digital-piano": ((149,), .40),
    "organ": ((150, 151, 152), .40), "synth": ((153,), .26), "harpsichord": ((155,), .40),
    "percussion": ((156,), .42), "drums": ((157, 158, 159), .32), "snare": ((160,), .48),
    "kick": ((163,), .48), "timpani": ((164,), .45), "hh": ((167,), .46),
    "tambourine": ((169,), .40), "bells": ((173, 195, 196), .44), "marimba": ((175,), .44),
    "glockenspiel": ((176,), .44), "brass": ((180,), .28), "french-horn": ((181,), .40),
    "trumpet": ((182,), .39), "trombone": ((183,), .40), "strings": ((184, 185), .28),
    "violin": ((186,), .43), "cello": ((188,), .38), "double-bass": ((189,), .42),
    "woodwind": ((190,), .30), "flute": ((191,), .36), "saxophone": ((192,), .40),
    "clarinet": ((193,), .40), "harp": ((194,), .38), "wind-chimes": ((201,), .43),
    "harmonica": ((203,), .40), "accordion": ((204,), .40), "whoosh": ((453,), .51),
}
REFINEMENTS = {
    "strings": {"violin": "violin", "viola": "viola", "cello": "cello", "double-bass": "orchestral double bass"},
    "woodwind": {"flute": "flute", "clarinet": "clarinet", "oboe": "oboe", "bassoon": "bassoon"},
    "brass": {"french-horn": "French horn", "trumpet": "trumpet", "trombone": "trombone", "tuba": "tuba"},
}
EFFECT_LABELS = {
    "riser": "a synthesized ascending sweep building tension in a song",
    "impact": "a cinematic electronic impact sound effect",
    "whoosh": "a brief synthetic swishing transition sound effect",
}
ELECTRONIC_LABELS = {
    "synth": "an electronic synthesizer playing music",
    "digital-piano": "an electric piano playing music",
    "drums": "a drum kit or electronic drum machine playing in music",
    "bass": "a bass guitar playing music",
}

FAMILY_PROMPTS = {
    "strings": ("a bowed string instrument playing music", "a bowed string section"),
    "woodwind": ("a woodwind instrument playing music", "a section of woodwind instruments"),
    "brass": ("a brass instrument playing music", "a section of brass instruments"),
}


@dataclass
class RecognitionResult:
    tracks: tuple[str, ...]
    uncertain: tuple[str, ...]
    has_audio: bool = True
    warning: str = ""
    evidence: tuple["Evidence", ...] = ()


@dataclass(frozen=True)
class Evidence:
    label: str
    model: str
    start: float
    end: float
    score: float


def recognize_passt(source: Path, model_dir: Path, quality: Quality, progress: ProgressCallback,
                    check_cancel: Callable[[], None], *, device: str = "auto") -> RecognitionResult:
    from stem_separator.backends.passt_openmic import CLASSES, RATE, predict

    progress(Progress("decode", "正在读取乐器识别用音频…"))
    audio = decode_audio(source, RATE, 1, check_cancel=check_cancel)[0]
    limit = {"fast": 6, "balanced": 18, "high": 48}[quality]
    segments = predict(audio, passt_asset(model_dir), progress, check_cancel, limit=limit, device_name=device)
    evidence = tuple(Evidence(label, "PaSST/OpenMIC", start, end, float(score))
                     for start, end, scores in segments for label, score in zip(CLASSES, scores, strict=True))
    confirmed, tentative = {}, []
    for index, label in enumerate(CLASSES):
        scores = [float(scores[index]) for _, _, scores in segments]
        if not scores:
            break
        strongest = max(scores)
        # One strong window can preserve a late entrance. Benchmarks tune the score cutoff.
        if label in HEAD_IDS and (strongest >= .70 or sum(score >= .48 for score in scores) >= 2):
            confirmed[label] = strongest
        elif label in HEAD_IDS and strongest >= .30:
            tentative.append(label)
    return RecognitionResult(non_overlapping_targets(confirmed), tuple(tentative), has_signal(audio), evidence=evidence)


def non_overlapping_targets(scores: dict[str, float]) -> tuple[str, ...]:
    return tuple(item.id for item in INSTRUMENTS
                 if item.id in scores and not GROUPS.get(item.id, set()) & scores.keys())


def classify_yamnet(patches: NDArray[np.float32]) -> dict[str, float]:
    detected = {}
    for track, (indices, minimum) in TAG_CLASSES.items():
        scores = patches[:, indices].max(axis=1)
        strongest = float(scores.max())
        threshold = minimum if len(scores) < 4 else min(minimum + .20, .85)
        if strongest >= threshold or np.count_nonzero(scores >= minimum) >= 2:
            detected[track] = strongest
    for family, children in REFINEMENTS.items():
        if family not in detected:
            continue
        for track in children.keys() & detected.keys():
            indices, _ = TAG_CLASSES[track]
            scores = patches[:, indices].max(axis=1)
            if not (np.count_nonzero(scores >= .32) >= 3 and np.count_nonzero(scores >= .50) >= 1):
                del detected[track]
    return detected


def recognize(source: Path, model_dir: Path, quality: Quality,
              progress: ProgressCallback, check_cancel: Callable[[], None], *,
              use_passt: bool = False) -> RecognitionResult:
    rate = 16000
    progress(Progress("decode", "正在读取音频…"))
    stereo = decode_audio(source, rate, 2, check_cancel=check_cancel)
    if not has_signal(stereo):
        return RecognitionResult((), (), has_audio=False)
    # Use the louder side when a stereo fold would erase meaningful audio.
    audio = stereo.mean(axis=0)
    if not has_signal(audio):
        audio = stereo[np.unravel_index(np.abs(stereo).argmax(), stereo.shape)[0]]
    try:
        result = recognize_audio(source, audio, model_dir, quality, progress, check_cancel)
    except (SeparationError, ImportError, OSError, RuntimeError, ValueError) as error:
        result = RecognitionResult((), (), warning=f"本地识别模型不可用。{error}")
    if use_passt and passt_asset(model_dir).path.is_file():
        try:
            enhanced = recognize_passt(source, model_dir, quality, progress, check_cancel)
            # Held-out evaluation did not justify promoting PaSST-only labels to confirmed.
            uncertain = tuple(dict.fromkeys(name for name in (*result.uncertain, *enhanced.tracks, *enhanced.uncertain)
                                            if name not in result.tracks))
            return RecognitionResult(result.tracks, uncertain, warning=result.warning,
                                     evidence=result.evidence + enhanced.evidence)
        except (SeparationError, ImportError, OSError, RuntimeError, ValueError) as error:
            result.warning += f" PaSST 复核不可用。{error}"
    return result


def recognize_audio(source, audio, model_dir, quality, progress, check_cancel) -> RecognitionResult:
    from ai_edge_litert.interpreter import Interpreter

    rate = 16000
    progress(Progress("load", "正在加载乐器识别模型…", model="YAMNet",
                      duration=len(audio) / rate, device="CPU"))
    assets = recognition_assets(model_dir, quality)
    with assets[0].open_checkpoint() as checkpoint:
        interpreter = Interpreter(model_content=checkpoint.read(), num_threads=2)
    interpreter.allocate_tensors()
    inp = interpreter.get_input_details()[0]
    out = interpreter.get_output_details()[0]["index"]
    width = int(inp["shape"][0])
    count = max(1, 1 + (len(audio) - width) // (rate // 2))
    offsets = np.unique(np.linspace(0, max(0, len(audio) - width), min(count, 600), dtype=np.int64))
    patches, scanned = [], []
    for index, offset in enumerate(offsets):
        if index % 16 == 0:
            progress(Progress("analysis", "正在识别乐器和声部…", index, len(offsets)))
        window = audio[offset:offset + width]
        if not has_signal(window):
            continue
        interpreter.set_tensor(inp["index"], np.pad(window, (0, width - len(window))))
        interpreter.invoke()
        patches.append(interpreter.get_tensor(out)[0])
        scanned.append(offset)
    if not patches:
        return RecognitionResult((), ())
    values = np.asarray(patches)
    yamnet = classify_yamnet(values)
    weak_tracks = set()
    families = set()
    for track, (indices, minimum) in TAG_CLASSES.items():
        strength = values[:, indices].max(axis=1)
        # Subthreshold signals are retained as evidence, never as confirmed tracks.
        if np.any(strength >= minimum * .35):
            weak_tracks.add(track)
            if track in REFINEMENTS:
                families.add(track)
    clap = {}
    uncertain = ()
    evidence = []
    for track, (indices, _) in TAG_CLASSES.items():
        scores = values[:, indices].max(axis=1)
        for index in np.argsort(scores)[-3:]:
            start = scanned[index]
            evidence.append(Evidence(track, "YAMNet", start / rate, min(len(audio), start + width) / rate,
                                     float(scores[index])))
    if quality != "fast":
        assets = recognition_assets(model_dir, "high")[1:]
        if all(asset.path.is_file() for asset in assets):
            try:
                progress(Progress("load", "正在加载细分乐器复核模型…", model="CLAP"))
                clap, uncertain, clap_evidence = refine_clap(
                    decode_audio(source, 48000, 1, check_cancel=check_cancel)[0], yamnet, families,
                    weak_tracks, assets, check_cancel, progress,
                )
                evidence.extend(clap_evidence)
            except (SeparationError, ImportError, OSError, RuntimeError, ValueError):
                progress(Progress("analysis", "本地复核模型不可用，继续使用初步识别。", model="YAMNet", device="CPU"))
    tracks = non_overlapping_targets(yamnet | clap)
    return RecognitionResult(tracks, tuple(name for name in uncertain if name not in tracks), evidence=tuple(evidence))


def refine_clap(audio: NDArray[np.float32], yamnet: dict[str, float], families: set[str], weak_tracks: set[str],
                assets: tuple[ModelCache, ...], check_cancel: Callable[[], None], progress: ProgressCallback,
                ) -> tuple[dict[str, float], tuple[str, ...], tuple[Evidence, ...]]:
    import torch
    from transformers import AutoTokenizer, ClapFeatureExtractor, ClapModel

    for asset in assets:
        check_cancel()
        with asset.open_checkpoint():
            pass
    model_dir = assets[0].path.parent
    extractor = ClapFeatureExtractor.from_pretrained(model_dir, local_files_only=True)
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    model = ClapModel.from_pretrained(model_dir, local_files_only=True).eval()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cpu":
        torch.set_num_threads(min(torch.get_num_threads(), 4))
    try:
        model.to(device)
    except torch.cuda.OutOfMemoryError:
        device = torch.device("cpu")
        model.to(device)
        torch.set_num_threads(min(torch.get_num_threads(), 4))
        torch.cuda.empty_cache()

    prompts = EFFECT_LABELS | ELECTRONIC_LABELS
    for family, labels in REFINEMENTS.items():
        if family in families or labels.keys() & yamnet.keys():
            prompts.update({track: f"the sound of a {name} playing in music" for track, name in labels.items()})
            for index, prompt in enumerate(FAMILY_PROMPTS[family]):
                prompts[f"{family}:{index}"] = prompt
    negatives = ("ordinary recorded music", "a general musical instrument", "a drum hit", "orchestral music",
                 "a bowed string section", "a section of wind instruments", "electronic music without discrete sound effects")
    texts = tokenizer([*prompts.values(), *negatives], padding=True, return_tensors="pt").to(device)
    width = 10 * extractor.sampling_rate
    end = max(0, len(audio) - width)
    count = min(16, 1 + (end + width // 2 - 1) // (width // 2))
    offsets = np.unique(np.linspace(0, end, count, dtype=np.int64))
    similarities = []
    device_label = "CUDA" if device.type == "cuda" else "CPU"
    message = "正在复核细分乐器与电子音效"
    with torch.inference_mode():
        check_cancel()
        text = torch.nn.functional.normalize(model.get_text_features(**texts), dim=-1)
        for index, offset in enumerate(offsets):
            check_cancel()
            progress(Progress("analysis", message, index, len(offsets), model="CLAP", device=device_label))
            clip = audio[offset:offset + width]
            features = extractor([clip], sampling_rate=extractor.sampling_rate, return_tensors="pt").to(device)
            embedding = torch.nn.functional.normalize(model.get_audio_features(**features), dim=-1)
            similarities.append((embedding @ text.T).cpu().numpy()[0])
    values = np.asarray(similarities)
    baseline = values[:, len(prompts):]
    prompt_indices = {track: index for index, track in enumerate(prompts)}
    scores = {}
    uncertain = []
    required = min(2, len(offsets))
    for track, index in prompt_indices.items():
        if ":" in track:
            continue
        affinity = values[:, index]
        other = [prompt_indices[name] for labels in REFINEMENTS.values() if track in labels
                 for name in labels if name != track]
        if track in ELECTRONIC_LABELS:
            hits = (affinity >= .34) & (affinity - baseline[:, :2].max(axis=1) >= .035)
        else:
            floor = baseline.max(axis=1)
            if other:
                floor = np.maximum(floor, values[:, other].max(axis=1))
            hits = (affinity >= .30) & (affinity - floor >= .025)
        if np.count_nonzero(hits) >= required:
            if other and track not in yamnet and track not in weak_tracks:
                uncertain.append(track)
            else:
                scores[track] = float(affinity.max())
        elif other:
            # Near ties are useful for manual review, especially for violin versus viola.
            above_background = affinity >= baseline.max(axis=1) + .035
            near_best = affinity >= values[:, other].max(axis=1) - .04
            if np.count_nonzero((affinity >= .34) & above_background & near_best) >= required:
                uncertain.append(track)
    for family in families:
        if family in yamnet or REFINEMENTS[family].keys() & (yamnet.keys() | scores.keys()):
            continue
        indices = [prompt_indices[f"{family}:{index}"] for index in range(len(FAMILY_PROMPTS[family]))]
        affinity = values[:, indices].max(axis=1)
        floor = baseline[:, :2].max(axis=1)
        if np.count_nonzero((affinity >= .31) & (affinity - floor >= .03)) >= required:
            scores[family] = float(affinity.max())
    evidence = tuple(Evidence(track, "LAION CLAP", offset / extractor.sampling_rate,
                              min(len(audio), offset + width) / extractor.sampling_rate, float(values[index, column]))
                     for track, column in prompt_indices.items() if ":" not in track
                     for index, offset in enumerate(offsets) if values[index, column] >= .20)
    return scores, tuple(uncertain), evidence
