"""Run the actual CREPE model against generated, non-copyrighted test signals.

Usage: .venv/bin/python scripts/benchmark_pitch.py [--device cpu|cuda]
No model downloads or expensive inference run in the normal unit-test suite.
This benchmark loads torchcrepe's packaged full weights and fails when core
regressions exceed the stated tolerances. Synthetic accuracy is not a promise
of equivalent real-singer accuracy; recordings with polyphony need listening QA.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stem_studio.pitch import SAMPLE_RATE, analyze_pitch  # noqa: E402


def synth(frequency, *, harmonics=(0.4, 0.7, 0.5, 0.25, 0.15), amplitude=0.3, noise=0.001):
    frequency = np.asarray(frequency)
    phase = 2 * np.pi * np.cumsum(frequency) / SAMPLE_RATE
    signal = sum(weight * np.sin((i + 1) * phase + i * 0.3) for i, weight in enumerate(harmonics))
    signal *= amplitude / max(float(np.max(np.abs(signal))), 1e-9)
    rng = np.random.default_rng(20260922)
    signal += noise * rng.standard_normal(len(signal))
    fade = min(160, len(signal) // 2)
    signal[:fade] *= np.linspace(0, 1, fade)
    signal[-fade:] *= np.linspace(1, 0, fade)
    return signal.astype(np.float32)


def cases():
    rate = SAMPLE_RATE
    scale = np.repeat(440 * 2 ** ((np.array([60, 62, 64, 65, 67, 69, 71, 72]) - 69) / 12), rate // 2)
    yield "half_second_scale", synth(scale), scale
    missing = np.full(rate * 2, 220.0)
    yield "missing_fundamental", synth(missing, harmonics=(0, 1.0, 0.65, 0.4, 0.2)), missing
    yield "strong_second_harmonic", synth(missing, harmonics=(0.025, 1.0, 0.25, 0.15, 0.1)), missing
    yield "noisy_voice_approximately_10db_snr", synth(missing, noise=0.035), missing
    octaves = np.repeat([220.0, 440.0, 220.0], rate // 2)
    yield "real_octave_changes", synth(octaves), octaves
    t = np.arange(rate * 2) / rate
    vibrato = 220 * 2 ** ((35 * np.sin(2 * np.pi * 5 * t)) / 1200)
    yield "vibrato_35_cents", synth(vibrato), vibrato
    scoop = np.full(rate, 220.0)
    scoop[:rate // 5] *= 2 ** (np.linspace(-100, 0, rate // 5) / 1200)
    yield "scooped_attack", synth(scoop), scoop
    quiet = np.full(rate, 196.0)
    yield "quiet_voice", synth(quiet, amplitude=0.0006, noise=0.000001), quiet
    low = np.full(rate, 82.4069)
    yield "low_voice_E2", synth(low), low
    rng = np.random.default_rng(42)
    unvoiced = np.concatenate((np.zeros(rate // 2), 0.03 * rng.standard_normal(rate // 2)))
    yield "silence_and_noise", unvoiced.astype(np.float32), np.full(len(unvoiced), np.nan)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--fixtures", type=Path, help="Save generated 10-second browser/API fixtures in this directory")
    parser.add_argument("--vocadito-dir", type=Path, help="Evaluate an independently downloaded Vocadito dataset (CC BY 4.0)")
    parser.add_argument("--track-ids", default="1,2,4,6,10,17,35,37", help="Comma-separated Vocadito tracks, or all")
    parser.add_argument("--real-only", action="store_true", help="Skip synthetic cases when evaluating Vocadito")
    args = parser.parse_args()
    if args.fixtures:
        args.fixtures.mkdir(parents=True, exist_ok=True)
        melody = np.repeat(440 * 2 ** ((np.array([60, 62, 64, 65, 67, 65, 64, 62, 60, 64]) - 69) / 12), SAMPLE_RATE)
        reference = synth(melody)
        take = np.concatenate((np.zeros(int(0.4 * SAMPLE_RATE)), synth(melody * 2 ** (-40 / 1200))))
        noise = np.random.default_rng(12).standard_normal(SAMPLE_RATE * 10) * 0.025
        for name, audio in (("reference-scale.wav", reference), ("take-flat40c-delayed400ms.wav", take), ("noise.wav", noise)):
            path = args.fixtures / name
            sf.write(path, audio, SAMPLE_RATE, subtype="PCM_16")
            print(json.dumps({"fixture": str(path.resolve())}), flush=True)
    reports = []
    with tempfile.TemporaryDirectory(prefix="stem-pitch-benchmark-") as temp:
        for name, audio, truth in ([] if args.real_only else cases()):
            path = Path(temp) / f"{name}.wav"
            sf.write(path, audio, SAMPLE_RATE, subtype="FLOAT")
            start = time.monotonic()
            result = analyze_pitch(path, device=args.device)
            elapsed = time.monotonic() - start
            frames = result["frames"]
            sample_indices = np.minimum((np.array([f["time"] for f in frames]) * SAMPLE_RATE).round().astype(int), len(truth) - 1)
            expected = truth[sample_indices]
            actual = np.array([f["hz"] if f["hz"] else np.nan for f in frames])
            # Exclude the centered 64ms analysis-window overlap at abrupt edges.
            usable = (sample_indices > 640) & (sample_indices < len(truth) - 640) & np.isfinite(expected)
            edges = np.flatnonzero(np.abs(np.diff(np.nan_to_num(truth))) > 10)
            for edge in edges:
                usable &= np.abs(sample_indices - edge) > 640
            tracked = usable & np.isfinite(actual)
            cents = np.abs(1200 * np.log2(actual[tracked] / expected[tracked]))
            report = {
                "case": name, "audio_seconds": round(len(audio) / SAMPLE_RATE, 2),
                "compute_seconds": round(elapsed, 2),
                "voiced_recall": round(float(tracked.sum() / max(1, usable.sum())), 4),
                "expected_voiced": bool(np.any(np.isfinite(expected))),
                "median_cents_error": round(float(np.median(cents)), 2) if len(cents) else None,
                "p95_cents_error": round(float(np.percentile(cents, 95)), 2) if len(cents) else None,
                "octave_error_frames": int(np.sum(cents > 600)),
                "false_voiced_frames": int(np.sum(np.isfinite(actual) & ~np.isfinite(expected))),
                "notes": [n["note"] for n in result["notes"]],
            }
            reports.append(report)
            print(json.dumps(report), flush=True)
    failures = [r["case"] for r in reports if
                (r["expected_voiced"] and
                 (r["median_cents_error"] is None or r["median_cents_error"] > 30 or r["p95_cents_error"] > 70 or r["voiced_recall"] < 0.90 or r["octave_error_frames"]))
                or r["false_voiced_frames"] > 5]
    print(json.dumps({"passed": not failures, "failures": failures, "total_compute_seconds": round(sum(r["compute_seconds"] for r in reports), 2)}))
    if args.vocadito_dir:
        # Source: Bittner et al., vocadito v3, CC BY 4.0.
        # https://zenodo.org/records/5578807 (media are never copied to this repo).
        track_ids = list(range(1, 41)) if args.track_ids == "all" else [int(value) for value in args.track_ids.split(",")]
        for track_id in track_ids:
            name = f"vocadito_{track_id}"
            annotation = np.loadtxt(args.vocadito_dir / "Annotations" / "F0" / f"{name}_f0.csv", delimiter=",")
            start = time.monotonic()
            result = analyze_pitch(args.vocadito_dir / "Audio" / f"{name}.wav", device=args.device)
            times = np.array([frame["time"] for frame in result["frames"]])
            position = np.searchsorted(annotation[:, 0], times)
            right = np.clip(position, 0, len(annotation) - 1)
            left = np.clip(position - 1, 0, len(annotation) - 1)
            nearest = np.where(np.abs(times - annotation[left, 0]) <= np.abs(times - annotation[right, 0]), left, right)
            expected = annotation[nearest, 1]
            actual = np.array([frame["hz"] or 0 for frame in result["frames"]])
            reference_voiced, estimated_voiced = expected > 0, actual > 0
            matched = reference_voiced & estimated_voiced
            errors = np.abs(1200 * np.log2(actual[matched] / expected[matched]))
            report = {
                "case": name, "audio_seconds": result["duration_seconds"],
                "compute_seconds": round(time.monotonic() - start, 2),
                "voiced_precision": round(float(matched.sum() / max(1, estimated_voiced.sum())), 4),
                "voiced_recall": round(float(matched.sum() / max(1, reference_voiced.sum())), 4),
                "voiced_pitch_accuracy_50c": round(float(np.sum(errors <= 50) / max(1, reference_voiced.sum())), 4),
                "median_cents_error": round(float(np.median(errors)), 2),
                "p95_cents_error": round(float(np.percentile(errors, 95)), 2),
                "octave_error_frames": int(np.sum(errors > 600)),
                "voiced_frames": int(reference_voiced.sum()),
            }
            print(json.dumps(report), flush=True)
        print(json.dumps({"note": "Real-recording evaluation is descriptive, not a guarantee for all voices; note boundaries are subjective. Data: Bittner et al. Vocadito v3, CC BY 4.0, https://zenodo.org/records/5578807"}))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
