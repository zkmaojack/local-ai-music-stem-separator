"""Measure offline AudioSep chunks, reconstruction, export and the preview mixer."""

import argparse
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np
import soundfile as sf
import sounddevice as sd
import torch
from torchaudio.functional import resample

from stem_separator.audio.decode import decode_audio
from stem_separator.audio.playback import BLOCK_SIZE, StemPlayer, load_preview
from stem_separator.backends.audiosep.effects import EXPORT_RATE, MODEL_RATE, EffectsBackend
from stem_separator.backends.demucs import resolve_device
from stem_separator.storage.export import export_stems
from stem_separator.storage.model_cache import effect_assets


def db(value):
    return np.round(20 * np.log10(np.maximum(1e-12, value)), 2)


def metrics(audio, rate):
    window = rate // 10
    power = np.mean(np.square(audio, dtype=np.float64), axis=0)
    offsets = np.arange(0, len(power), window)
    sizes = np.diff(np.r_[offsets, len(power)])
    rms_db = db(np.sqrt(np.add.reduceat(power, offsets) / sizes))
    loudness_window = min(3 * rate, len(power))
    integrated = np.r_[0., np.cumsum(power)]
    starts = np.arange(0, len(power) - loudness_window + 1, rate)
    energy = np.maximum(0., integrated[starts + loudness_window] - integrated[starts])
    short_term = db(np.sqrt(energy / loudness_window))
    return {
        "frames": len(power), "sample_rate": rate, "peak_dbfs": float(db(np.abs(audio).max())),
        "rms_dbfs": float(db(np.sqrt(power.mean()))), "samples_at_or_over_full_scale": int(np.sum(np.abs(audio) >= 1)),
        "rms_100ms_dbfs": rms_db.tolist(),
        "unweighted_3s_rms_dbfs": short_term.tolist(),
        "rms_change_max_db_100ms": float(np.max(np.abs(np.diff(rms_db)), initial=0)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--model-dir", type=Path, default=Path(".model-cache"))
    parser.add_argument("--output", type=Path, required=True, help="New directory for JSON and comparison WAV files")
    parser.add_argument("--effect", choices=("whoosh", "impact", "riser"), default="whoosh")
    parser.add_argument("--seconds", type=float, default=24)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    args.output.mkdir(parents=True)
    audio = decode_audio(args.source, EXPORT_RATE, 2)[:, :round(args.seconds * EXPORT_RATE)].copy()
    try:
        with sf.SoundFile(args.source) as file:
            original_rate = file.samplerate
            original = file.read(max(1, round(args.seconds * original_rate)), dtype="float32", always_2d=True)
        original_stats = metrics(original.T, original_rate)
    except sf.SoundFileError:
        original_stats = {"note": "Container opened only by FFmpeg; see decoded audio."}
    report = {
        "source": str(args.source), "effect": args.effect, "original": original_stats,
        "decoded": metrics(audio, EXPORT_RATE),
        "model_input": metrics(resample(torch.from_numpy(audio), EXPORT_RATE, MODEL_RATE).numpy(), MODEL_RATE),
    }
    backend = EffectsBackend(effect_assets(args.model_dir), (args.effect,))
    backend.load()
    raw = []

    def capture(_model, _input, result):
        raw.append(result["waveform"][:, 0].detach().float().cpu().numpy())

    handle = backend.model.register_forward_hook(capture)
    try:
        device = resolve_device(args.device)
        seconds = 5 if device.type == "cuda" else backend._cpu_seconds()
        stems = backend._infer(audio, device, seconds, lambda _: None)
    finally:
        handle.remove()
        backend.unload()
    report["raw_model_chunks"] = [metrics(block, MODEL_RATE) for block in raw]
    report["reconstructed"] = metrics(stems[args.effect], EXPORT_RATE)
    report["mixture_residual_peak"] = float(np.max(np.abs(audio - sum(stems.values()))))
    chunk = round(seconds * MODEL_RATE)
    context = MODEL_RATE // 2
    hop = chunk - 2 * context
    hard_join = np.concatenate([block[:, context:context + hop] for block in raw], axis=1)
    model_frames = (audio.shape[-1] * MODEL_RATE + EXPORT_RATE - 1) // EXPORT_RATE
    hard_join = resample(torch.from_numpy(hard_join[:, :model_frames].copy()), MODEL_RATE,
                         EXPORT_RATE).numpy()[:, :audio.shape[-1]]
    report["hard_join"] = metrics(hard_join, EXPORT_RATE)
    report["seams"] = []
    # Both runs measure at the original hard-stitch positions and record aligned model views.
    for index, (a, b) in enumerate(zip(raw, raw[1:]), 1):
        sample = round(index * hop * EXPORT_RATE / MODEL_RATE)
        signal = stems[args.effect]
        if sample >= signal.shape[-1]:
            break
        jump = float(np.max(np.abs(signal[:, sample] - signal[:, sample - 1])))
        region = signal[:, max(0, sample - 2205):sample + 2205]
        p95 = float(np.percentile(np.abs(np.diff(region, axis=1)), 95))
        report["seams"].append({
            "time_s": sample / EXPORT_RATE, "estimate_disagreement": float(np.max(np.abs(a[:, context + hop] - b[:, context]))),
            "sample_jump": jump, "local_p95_sample_step": p95,
            "hard_join_sample_jump": float(np.max(np.abs(hard_join[:, sample] - hard_join[:, sample - 1]))),
        })
    paths, gain = export_stems(args.output / "wav", stems, backend.spec, audio.shape[-1])
    # Match the export gain so both comparison files can be played at the same level.
    sf.write(args.output / "hard-join-float.wav", (hard_join * gain).T, EXPORT_RATE, subtype="FLOAT")
    exported = sf.read(paths[args.effect], dtype="float32", always_2d=True)[0].T
    report["exported"] = metrics(exported, EXPORT_RATE)
    report["global_export_gain"] = gain
    report["export_quantization_error_peak"] = float(np.max(np.abs(exported - gain * stems[args.effect])))
    with TemporaryDirectory(dir=args.output) as cache:
        preview = load_preview(list(paths.values()), Path(cache))
        try:
            player = StemPlayer(preview)
            player.gains = (1., 0.)
            block = np.empty((BLOCK_SIZE, 2), dtype=np.float32)
            mix = np.empty_like(exported)
            clock = SimpleNamespace(outputBufferDacTime=0.)
            for start in range(0, preview.frames, BLOCK_SIZE):
                size = min(BLOCK_SIZE, preview.frames - start)
                try:
                    player._mix(block, size, clock, None)
                except sd.CallbackStop:
                    pass
                mix[:, start:start + size] = block[:size].T
            report["playback_callback"] = metrics(mix, EXPORT_RATE)
            report["playback_error_peak"] = float(np.max(np.abs(mix - exported)))
        finally:
            preview.close()
    (args.output / "diagnostics.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf8")


if __name__ == "__main__":
    main()
