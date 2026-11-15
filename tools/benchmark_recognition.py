"""Build a deterministic BabySlakh excerpt set and compare local recognizers.

Pass the extracted babyslakh_16k folder from https://zenodo.org/records/4603870.
All generated excerpts and metrics go in the requested output directory.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from statistics import median
import time

import numpy as np
import soundfile as sf
import yaml

from stem_separator.application.recognition import recognize, recognize_passt
from stem_separator.application.runtime_stats import peak_rss_bytes

CLASSES = ("bass", "cello", "clarinet", "drums", "flute", "guitar", "organ", "piano", "saxophone",
           "synth", "trombone", "trumpet", "violin", "vocal")


def instrument(meta):
    if meta.get("is_drum"):
        return "drums"
    program = meta["program_num"]
    if 0 <= program <= 5:
        return "piano"
    if 16 <= program <= 20:
        return "organ"
    if 24 <= program <= 31:
        return "guitar"
    if 32 <= program <= 38:
        return "bass"
    if 80 <= program <= 103 or program == 39:
        return "synth"
    return {40: "violin", 41: "viola", 42: "cello", 56: "trumpet", 57: "trombone", 64: "saxophone",
            65: "saxophone", 66: "saxophone", 67: "saxophone", 71: "clarinet", 73: "flute"}.get(program, "other")


def prepare(slakh, output):
    for index, folder in enumerate(sorted(slakh.glob("Track*"))):
        metadata = yaml.safe_load((folder / "metadata.yaml").read_text(encoding="utf-8"))
        # A single preselected excerpt per source song keeps calibration and holdout independent.
        info = sf.info(folder / "mix.wav")
        start = max(0, int(info.frames * .35) - info.samplerate * 4)
        samples, rate = sf.read(folder / "mix.wav", start=start, frames=16 * info.samplerate, dtype="float32")
        labels, references = set(), {}
        for name, track in metadata["stems"].items():
            stem = folder / "stems" / f"{name}.wav"
            if not stem.is_file():
                continue  # BabySlakh includes metadata for some absent audio files.
            audio, sr = sf.read(stem, start=start, frames=len(samples), dtype="float32")
            if sr != rate or len(audio) != len(samples):
                raise ValueError(f"Unaligned reference: {folder.name}/{name}")
            label = instrument(track)
            if np.sqrt(np.mean(audio**2)) >= max(1e-5, .015 * np.sqrt(np.mean(samples**2))):
                labels.add(label)
            references[label] = references[label] + audio if label in references else audio
        target = output / ("calibration" if index < 8 else "test") / folder.name
        target.mkdir(parents=True, exist_ok=True)
        # Use the provided stems to rebuild the input and keep the reference complete.
        samples = np.sum(list(references.values()), axis=0, dtype=np.float32)
        sf.write(target / "mix.wav", samples, rate, subtype="FLOAT")
        for name, audio in references.items():
            sf.write(target / f"{name}.wav", audio, rate, subtype="FLOAT")
        (target / "labels.json").write_text(json.dumps(sorted(labels)), encoding="utf-8")


def scores(rows, prediction, labels=CLASSES):
    classes = {}
    for name in labels:
        tp = sum(name in row[prediction] and name in row["labels"] for row in rows)
        fp = sum(name in row[prediction] and name not in row["labels"] for row in rows)
        fn = sum(name not in row[prediction] and name in row["labels"] for row in rows)
        classes[name] = {"tp": tp, "fp": fp, "fn": fn, "f1": 2 * tp / max(1, 2 * tp + fp + fn)}
    tp, fp, fn = (sum(row[key] for row in classes.values()) for key in ("tp", "fp", "fn"))
    return {"precision": tp / max(1, tp + fp), "recall": tp / max(1, tp + fn),
            "f1": 2 * tp / max(1, 2 * tp + fp + fn), "false_positives": fp, "false_negatives": fn,
            "per_class": classes, "median_seconds": median(row["seconds"] for row in rows)}


def evaluate(args):
    import torch

    torch.set_num_threads(4)
    dataset = args.output
    results = {split: [] for split in ("calibration", "test")}
    for split, rows in results.items():
        for song in sorted((dataset / split).glob("*/labels.json")):
            labels = json.loads(song.read_text(encoding="utf-8"))
            source = song.with_name("mix.wav")
            start = time.perf_counter()
            def ignore(*_):
                pass
            if args.engine == "passt":
                result = recognize_passt(source, args.models, "balanced", ignore, ignore, device=args.device)
            else:
                mode = "fast" if args.engine == "yamnet" else "balanced"
                result = recognize(source, args.models, mode, ignore, ignore)
            if result.warning:
                raise RuntimeError(result.warning)
            row = {"song": song.parent.name, "labels": labels, "tracks": result.tracks,
                   "uncertain": result.uncertain, "seconds": time.perf_counter() - start,
                   "evidence": [asdict(item) for item in result.evidence]}
            rows.append(row)
            print(split, row["song"], row["tracks"], round(row["seconds"], 2), flush=True)
    metrics = {key: scores(rows, "tracks", args.labels) for key, rows in results.items() if rows}
    path = dataset / f"{args.engine}-{args.device}.json"
    path.write_text(json.dumps({"metrics": metrics, "rows": results, "peak_rss_bytes": peak_rss_bytes(),
                                "peak_cuda_bytes": torch.cuda.max_memory_allocated() if torch.cuda.is_initialized()
                                else None}, indent=2), encoding="utf-8")
    print(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--prepare", type=Path)
    parser.add_argument("--models", type=Path, default=Path(".model-cache"))
    parser.add_argument("--engine", choices=("yamnet", "clap", "passt"))
    parser.add_argument("--device", choices=("auto", "cpu"), default="auto")
    parser.add_argument("--labels", nargs="+", default=CLASSES)
    args = parser.parse_args()
    if args.prepare:
        prepare(args.prepare, args.output)
    if args.engine:
        evaluate(args)


if __name__ == "__main__":
    main()
