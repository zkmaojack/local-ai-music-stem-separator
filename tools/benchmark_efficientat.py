"""Evaluate the reviewed EfficientAT mn04_as AudioSet model on the same local excerpts.

Use a checkout of fschmid56/EfficientAT at a425fdce92572e602a1d5634799bd9f1f2efa806
and a compatible torchvision on PYTHONPATH. No training or network access is needed.
"""
import argparse
from contextlib import redirect_stdout
from hashlib import file_digest
import io
import json
import os
from pathlib import Path
import sys
from time import perf_counter

import torch

from benchmark_recognition import scores
from stem_separator.application.runtime_stats import peak_rss_bytes
from stem_separator.audio.decode import decode_audio
from stem_separator.backends.passt_openmic import RATE, segment_starts

AUDIOSET_CLASSES = {
    "bass": (142,), "cello": (193,), "clarinet": (198,), "drums": (162, 163, 164), "flute": (196,),
    "guitar": (140, 141, 143), "organ": (155, 156, 157), "piano": (153,), "saxophone": (197,),
    "synth": (158,), "trombone": (188,), "trumpet": (187,), "violin": (191,), "vocal": (27, 28, 32, 33, 36),
}
SHA256 = "899a8c6217063f6941793102a2780b3dd788ab11fce468d9cf5f0577c453321c"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("excerpts", type=Path)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("weights", type=Path)
    opts = parser.parse_args()
    path, root, weight = (value.resolve() for value in (opts.excerpts, opts.checkout, opts.weights))
    torch.set_num_threads(4)
    # Upstream's label reader opens a relative CSV. Execute only the reviewed checkout.
    sys.path.insert(0, str(root))
    os.chdir(root)
    from models.mn.model import get_model
    from models.preprocess import AugmentMelSTFT

    started = perf_counter()
    with weight.open("rb") as file:
        if file_digest(file, "sha256").hexdigest() != SHA256:
            raise ValueError("EfficientAT checkpoint SHA-256 mismatch")
        file.seek(0)
        state = torch.load(file, map_location="cpu", weights_only=True)
    with redirect_stdout(io.StringIO()):
        model = get_model(width_mult=.4).eval()
        mel = AugmentMelSTFT().eval()
    model.load_state_dict(state, strict=True)
    load_seconds = perf_counter() - started
    results = {}
    for split in ("calibration", "test"):
        rows = []
        for labels in sorted((path / split).glob("*/labels.json")):
            start = perf_counter()
            clip = decode_audio(labels.with_name("mix.wav"), RATE, 1)[0]
            logits = []
            for offset in segment_starts(len(clip), 18):
                with torch.inference_mode():
                    spec = mel(torch.from_numpy(clip[offset:offset + RATE * 10])[None])
                    logits.append(model(spec[:, None])[0].sigmoid().numpy()[0])
            raw = {name: max(float(values[list(ids)].max()) for values in logits) for name, ids in AUDIOSET_CLASSES.items()}
            rows.append({"song": labels.parent.name, "labels": json.loads(labels.read_text()),
                         "tracks": [name for name, value in raw.items() if value >= .5], "scores": raw,
                         "seconds": perf_counter() - start})
        results[split] = rows
    data = {"metrics": {split: scores(rows, "tracks") for split, rows in results.items()},
            "rows": results, "load_seconds": load_seconds, "peak_rss_bytes": peak_rss_bytes()}
    (path / "efficientat-cpu.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
