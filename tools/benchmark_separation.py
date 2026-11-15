"""Compare the original mix and separated stems against local WAV references."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter

import numpy as np

from stem_separator.audio.decode import decode_audio


def si_sdr(estimate, reference):
    y, x = (a.astype(np.float64).ravel() for a in (estimate, reference))
    y, x = y - y.mean(), x - x.mean()
    power = np.dot(x, x)
    if power < 1e-10:
        return None
    projected = x * (np.dot(x, y) / power)
    residual = y - projected
    return float(10 * np.log10(max(1e-12, np.dot(projected, projected)) / max(1e-12, np.dot(residual, residual))))


def projection_diagnostics(estimate, name, references):
    """Estimate interference from the known references; remainder can include other sources."""
    if len(references) < 2:
        return {}
    keys = list(references)
    matrix = np.column_stack([references[key].ravel() for key in keys]).astype(np.float64)
    matrix -= matrix.mean(axis=0)
    if np.linalg.norm(matrix[:, keys.index(name)]) < 1e-5:
        return {}
    signal = estimate.ravel().astype(np.float64)
    signal -= signal.mean()
    coefficients = np.linalg.lstsq(matrix.T @ matrix, matrix.T @ signal, rcond=1e-8)[0]
    target = matrix[:, keys.index(name)] * coefficients[keys.index(name)]
    represented = matrix @ coefficients
    numerator = max(1e-12, np.dot(target, target))
    others = represented - target
    unexplained = signal - represented
    return {"reference_interference_ratio_db": float(10 * np.log10(numerator / max(1e-12, np.dot(others, others)))),
            "unexplained_energy_ratio_db": float(10 * np.log10(numerator / max(1e-12, np.dot(unexplained, unexplained))))}


def compare(root, source, reference_paths, destination, quality, model, targets, device):
    start = perf_counter()
    command = [sys.executable, "-m", "stem_separator.gui.worker", "separate", str(root), quality,
               str(source), str(destination), "automatic", model, *targets]
    env = os.environ.copy()
    env["STEM_SEPARATOR_BENCH_DEVICE"] = device
    completed = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", env=env)
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith('{"event"')]
    if completed.returncode or not events or events[-1]["event"] != "result":
        raise RuntimeError(f"{events[-1] if events else ''}\n{completed.stderr}")
    result = events[-1]
    result["worker_wall_seconds"] = perf_counter() - start
    rate = result["sample_rate"]
    mix = decode_audio(source, rate, 2)
    result["reference"] = {}
    references = {name: decode_audio(path, rate, 2) for name, path in reference_paths.items()}
    for name, ref in references.items():
        if ref.shape != mix.shape:
            raise ValueError(f"Reference length mismatch: {reference_paths[name]}")
        estimate_path = result["paths"].get(name)
        row = {"original_mix_si_sdr_db": si_sdr(mix, ref),
               "original_projection": projection_diagnostics(mix, name, references)}
        if estimate_path:
            estimate = decode_audio(Path(estimate_path), rate, 2)
            row["separated_si_sdr_db"] = si_sdr(estimate, ref)
            row["separated_projection"] = projection_diagnostics(estimate, name, references)
        result["reference"][name] = row
    outputs = [decode_audio(Path(path), rate, 2) for path in result["paths"].values()]
    remix = np.sum(outputs, axis=0)
    result["remix_error_rms"] = float(np.sqrt(np.mean((remix - mix * result["export_gain"]) ** 2)))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--models", type=Path, default=Path(".model-cache"))
    parser.add_argument("--quality", choices=("fast", "balanced", "high"), default="balanced")
    parser.add_argument("--model", choices=("auto", "demucs", "subroformer", "detailed"), default="auto")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--reference", action="append", default=[], metavar="STEM=PATH")
    options = parser.parse_args()
    references = {name: Path(path) for name, path in (entry.split('=', 1) for entry in options.reference)}
    result = compare(options.models, options.source, references, options.output, options.quality, options.model,
                     options.target, options.device)
    path = options.output / (options.source.stem + "-report.json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
