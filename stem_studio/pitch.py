"""Monophonic singing pitch analysis without snapping the performance to notes.

CREPE full supplies acoustic evidence; our sequence decoder allows real leaps,
uses deterministic sub-bin interpolation (no pitch dither), and does not carry
pitch through silence. See https://github.com/maxrmorrison/torchcrepe and the
CREPE paper https://arxiv.org/abs/1802.06182. Confidence is model evidence, not a
calibrated probability or a singing-quality score.
"""

from __future__ import annotations

import math
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000
HOP_LENGTH = 160
HOP_SECONDS = HOP_LENGTH / SAMPLE_RATE
WINDOW_SIZE = 1024
MAX_ANALYSIS_SECONDS = 90
ENGINE = "CREPE full + waveform consensus / continuous-pitch decoder v1"
_CENTS = 1997.3794084376191 + 20 * np.arange(360, dtype=np.float64)
_BIN_HZ = 10 * 2 ** (_CENTS / 1200)
_INFERENCE_LOCK = threading.Lock()
Progress = Callable[[float, str], None]


def hz_to_midi(hz: float) -> float:
    if not math.isfinite(hz) or hz <= 0:
        raise ValueError("Frequency must be a positive finite number.")
    return 69 + 12 * math.log2(hz / 440)


def midi_to_hz(midi: float) -> float:
    if not math.isfinite(midi):
        raise ValueError("Pitch must be a finite number.")
    return 440 * 2 ** ((midi - 69) / 12)


def midi_to_note(midi: float) -> str:
    if not math.isfinite(midi):
        raise ValueError("Pitch must be a finite number.")
    number = math.floor(midi + 0.5)
    return f"{('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')[number % 12]}{number // 12 - 1}"


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.flatnonzero(np.diff(np.pad(mask.astype(np.int8), (1, 1))))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist(), strict=True))


def _load_audio(path: Path) -> np.ndarray:
    if not path.is_file():
        raise ValueError("The recording could not be found.")
    # Read one extra frame to distinguish a 90 s clip from an oversized input.
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0", "-vn",
         "-t", str(MAX_ANALYSIS_SECONDS + 0.1), "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "f32le", "pipe:1"],
        capture_output=True, timeout=60,
    )
    if result.returncode:
        raise ValueError("This recording could not be decoded. Try an audio file such as WAV, MP3, or M4A.")
    audio = np.frombuffer(result.stdout, dtype="<f4").copy()
    if len(audio) > MAX_ANALYSIS_SECONDS * SAMPLE_RATE:
        raise ValueError(f"Choose a passage of {MAX_ANALYSIS_SECONDS} seconds or less.")
    if len(audio) < SAMPLE_RATE // 5 or not np.isfinite(audio).all():
        raise ValueError("Record at least 0.2 seconds of usable audio.")
    return audio


def _rms_db(audio: np.ndarray, frames: int) -> np.ndarray:
    # A microphone's DC offset is not sound. Center before padding as CREPE
    # also mean-centers its input; otherwise biased silence can look energetic.
    centered = audio.astype(np.float64) - float(np.mean(audio))
    padded = np.pad(centered, (WINDOW_SIZE // 2, WINDOW_SIZE // 2))
    integral = np.concatenate(([0.0], np.cumsum(padded * padded)))
    starts = np.arange(frames) * HOP_LENGTH
    power = (integral[starts + WINDOW_SIZE] - integral[starts]) / WINDOW_SIZE
    return 10 * np.log10(np.maximum(power, 1e-16))


def _infer_activations(audio: np.ndarray, device: str, progress: Progress | None) -> np.ndarray:
    # Lazy imports keep ordinary edit/mix routes and unit tests lightweight.
    import torch
    import torchcrepe

    if device != "cpu" and not device.startswith("cuda"):
        raise ValueError("Pitch analysis supports the CPU or a CUDA GPU.")
    batch_size = 128 if device.startswith("cuda") else 32
    count = 1 + len(audio) // HOP_LENGTH
    batches: list[np.ndarray] = []
    # torchcrepe caches one mutable model globally. Serialize device placement
    # and inference, including paired reference/take analyses in one process.
    with _INFERENCE_LOCK, torch.inference_mode():
        if device == "cpu" and torch.get_num_threads() > 4:
            torch.set_num_threads(4)
        tensor = torch.from_numpy(audio).unsqueeze(0)
        for frames in torchcrepe.preprocess(
            tensor, SAMPLE_RATE, HOP_LENGTH, batch_size=batch_size, device=device,
        ):
            activations = torchcrepe.infer(frames, model="full", device=device)
            batches.append(activations.detach().cpu().numpy())
            if progress:
                progress(0.08 + 0.72 * min(sum(len(batch) for batch in batches) / count, 1), "Tracing your voice")
    return np.concatenate(batches, axis=0)


def _weighted_hz(activations: np.ndarray, bins: np.ndarray) -> np.ndarray:
    offsets = np.arange(-4, 5)
    indices = bins[:, None] + offsets
    valid = (indices >= 0) & (indices < 360)
    indices = np.clip(indices, 0, 359)
    weights = activations[np.arange(len(bins))[:, None], indices] * valid
    cents = (weights * _CENTS[indices]).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-12)
    return 10 * 2 ** (cents / 1200)


def _viterbi_bins(activations: np.ndarray, allowed: np.ndarray) -> np.ndarray:
    """Decode all frames together with a finite penalty for any real leap.

    The stock decoder prohibits jumps above ~2 semitones per frame and decodes
    inference batches separately. A bounded transition cost permits abrupt
    octave changes when their acoustic evidence persists, without forcing a
    fictitious staircase between notes. Emissions use the original positive
    CREPE activations, not a second sigmoid or a softmax of probabilities.
    """
    bins = np.flatnonzero(allowed)
    emission = np.log(np.maximum(activations[:, bins], 1e-7))
    distance = np.abs(bins[:, None] - bins[None, :])
    cost = np.minimum(distance * 0.12, 4.5).astype(np.float32)
    previous = emission[0]
    parents = np.empty((len(emission), len(bins)), dtype=np.int16)
    columns = np.arange(len(bins))
    for index in range(1, len(emission)):
        candidates = previous[:, None] - cost
        best = candidates.argmax(axis=0)
        parents[index] = best
        previous = candidates[best, columns] + emission[index]
        previous -= previous.max()
    result = np.empty(len(emission), dtype=np.int64)
    state = previous.argmax()
    for index in range(len(emission) - 1, -1, -1):
        result[index] = bins[state]
        if index:
            state = parents[index, state]
    return result


def _correct_octave_glitches(midi: np.ndarray) -> np.ndarray:
    """Repair only <=40 ms 2x/3x excursions bracketed by the same note.

    A sustained leap, ordinary note change, or a change across a breath is
    untouched. This is intentionally too conservative to repair all errors.
    """
    result = midi.copy()
    for begin, end in _runs(np.isfinite(midi)):
        index = begin + 3
        while index < end - 3:
            baseline = float(np.median(result[index - 3:index]))
            delta = result[index] - baseline
            shifts = np.array([-12 * math.log2(3), -12, 12, 12 * math.log2(3)])
            harmonic_shift = float(shifts[np.argmin(np.abs(shifts - delta))])
            if abs(delta - harmonic_shift) > 0.65:
                index += 1
                continue
            stop = index
            while stop < min(index + 5, end) and abs(result[stop] - baseline - harmonic_shift) < 0.65:
                stop += 1
            if stop - index <= 4 and stop + 3 <= end:
                after = result[stop:stop + 3]
                if np.max(np.abs(after - baseline)) < 0.45:
                    result[index:stop] -= harmonic_shift
                    index = stop
                    continue
            index += 1
    return result


def _smooth_midi(midi: np.ndarray) -> np.ndarray:
    corrected = _correct_octave_glitches(midi)
    result = corrected.copy()
    # A centered 30 ms median removes one-frame spikes without averaging
    # different notes together or flattening normal vibrato and portamento.
    for begin, end in _runs(np.isfinite(corrected)):
        for index in range(begin + 1, end - 1):
            result[index] = np.median(corrected[index - 1:index + 2])
    return result


def _periodicity(window: np.ndarray, lag: float) -> float:
    """Normalized correlation at a fractional period, with no FFT-bin snapping."""
    positions = np.arange(max(0, int(len(window) - lag)))
    if len(positions) < 128:
        return 0.0
    left = window[:len(positions)]
    right = np.interp(positions + lag, np.arange(len(window)), window)
    norm = float(np.dot(left, left) * np.dot(right, right))
    return float(np.dot(left, right) / math.sqrt(norm)) if norm > 1e-20 else 0.0


def _harmonic_consensus(midi: np.ndarray, audio: np.ndarray, fmin: float) -> np.ndarray:
    """Correct neural 2x/3x errors only with independent waveform evidence.

    True high notes repeat equally well at one and two periods. If a longer
    period matches *materially better*, the shorter estimate missed part of
    the waveform. Require sustained support and a strong correlation rather
    than assuming that every melodic leap is an octave mistake. This also
    handles a sustained harmonic error that temporal smoothing cannot fix.
    """
    result = midi.copy()
    factors = np.ones(len(midi), dtype=np.int8)
    strong = np.zeros(len(midi), dtype=bool)
    refined = np.full(len(midi), np.nan)
    padded = np.pad(audio.astype(np.float64), (WINDOW_SIZE // 2, WINDOW_SIZE // 2))
    for index in np.flatnonzero(np.isfinite(midi)):
        window = padded[index * HOP_LENGTH:index * HOP_LENGTH + WINDOW_SIZE]
        window = window - window.mean()
        hz = midi_to_hz(float(midi[index]))
        lag = SAMPLE_RATE / hz
        base_correlation = _periodicity(window, lag)
        best_correlation = base_correlation
        for factor in (2, 3):
            if hz / factor < fmin:
                continue
            candidate = _periodicity(window, lag * factor)
            improvement = candidate - base_correlation
            supported = (candidate >= 0.91 and improvement >= 0.015) or (candidate >= 0.85 and improvement >= 0.08)
            if not supported or candidate <= best_correlation:
                continue
            best_correlation = candidate
            factors[index] = factor
            strong[index] = (candidate >= 0.935 and improvement >= 0.03) or (candidate >= 0.90 and improvement >= 0.10)
        if factors[index] != 1:
            candidate_lag = lag * factors[index]
            # Refine only a supported correction, retaining the neural
            # sub-bin estimate everywhere else. This is not note quantization.
            lags = candidate_lag * np.linspace(0.98, 1.02, 9)
            correlations = [_periodicity(window, value) for value in lags]
            best = int(np.argmax(correlations))
            offset = 0.0
            if 0 < best < len(lags) - 1:
                left, center, right = correlations[best - 1:best + 2]
                denominator = left - 2 * center + right
                if abs(denominator) > 1e-12:
                    offset = float(np.clip(0.5 * (left - right) / denominator, -1, 1))
            refined[index] = hz_to_midi(SAMPLE_RATE / (lags[best] + offset * (lags[1] - lags[0])))
    for factor in (2, 3):
        for begin, end in _runs(factors == factor):
            if end - begin < 3 or np.sum(strong[begin:end]) < 3:
                continue
            if end - begin < 8:
                # A centered window may contain the previous note during a
                # real attack. Short corrections need matching context on
                # both sides so that this overlap cannot erase the new note.
                before = midi[max(0, begin - 8):begin]
                after = midi[end:min(end + 8, len(midi))]
                before, after = before[np.isfinite(before)], after[np.isfinite(after)]
                center = float(np.median(refined[begin:end]))
                if len(before) < 3 or len(after) < 3:
                    continue
                if abs(float(np.median(before)) - center) > 0.8 or abs(float(np.median(after)) - center) > 0.8:
                    continue
            result[begin:end] = refined[begin:end]
    return result


def _track_from_activations(
    activations: np.ndarray, rms: np.ndarray, fmin: float, fmax: float,
    *, audio: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    allowed = (_BIN_HZ >= fmin) & (_BIN_HZ <= fmax)
    evidence = np.clip(activations.astype(np.float64), 0, 1)
    evidence[:, ~allowed] = 0
    raw_bins = evidence.argmax(axis=1)
    raw_confidence = evidence.max(axis=1)
    # Relative gate handles very quiet recordings without normalizing ambient
    # noise into voice; the neural evidence remains the main voicing decision.
    floor_db = max(-85.0, float(np.percentile(rms, 95)) - 45.0)
    candidate = (rms > floor_db) & (raw_confidence >= 0.18)
    bins = raw_bins.copy()
    for begin, end in _runs(candidate):
        bins[begin:end] = _viterbi_bins(evidence[begin:end], allowed)
    confidence = evidence[np.arange(len(bins)), bins]
    # A 50 ms voiced island is too short to identify a sung note reliably.
    voiced = candidate & (confidence >= 0.21)
    for begin, end in _runs(voiced):
        if end - begin < 5 or float(np.max(confidence[begin:end])) < 0.30:
            voiced[begin:end] = False
    hz = _weighted_hz(evidence, bins)
    raw_hz = _weighted_hz(evidence, raw_bins)
    midi = 69 + 12 * np.log2(np.maximum(hz, 1e-9) / 440)
    midi[~voiced] = np.nan
    raw_hz[~candidate] = np.nan
    if audio is not None:
        midi = _harmonic_consensus(midi, audio, fmin)
    cleaned = _smooth_midi(midi)
    return cleaned, raw_hz, np.where(rms > floor_db, confidence, 0)


def _notes(midi: np.ndarray, duration: float) -> list[dict]:
    """Describe sustained regions, while the frame curve retains ornaments.

    Note names are labels only: no quantization is applied to the plotted F0.
    Hysteresis prevents a vibrato around a semitone boundary from turning into
    dozens of fake notes. Short leading scoops are attached to the held note.
    """
    notes = []
    for begin, end in _runs(np.isfinite(midi)):
        if end - begin < 12:
            continue
        smoothed = np.array([
            np.median(midi[max(begin, i - 4):min(end, i + 5)]) for i in range(begin, end)
        ])
        regions: list[tuple[int, int]] = []
        start = 0
        anchor = float(np.median(smoothed[:min(20, len(smoothed))]))
        index = 1
        while index + 7 < len(smoothed):
            upcoming = smoothed[index:index + 8]
            if np.all(upcoming > anchor + 0.65) or np.all(upcoming < anchor - 0.65):
                regions.append((start, index))
                start = index
                anchor = float(np.median(smoothed[index:min(index + 20, len(smoothed))]))
                index += 8
            else:
                index += 1
        regions.append((start, len(smoothed)))
        # A short smooth attack is useful feedback, not a separate bad note.
        if len(regions) > 1 and regions[0][1] < 18:
            a, b = regions[0], regions[1]
            if abs(float(np.median(smoothed[a[0]:a[1]]) - np.median(smoothed[b[0]:b[1]]))) < 2:
                regions[:2] = [(a[0], b[1])]
        for left, right in regions:
            if right - left < 12:
                continue
            values = midi[begin + left:begin + right]
            held = values[min(10, len(values) // 3):]
            center = float(np.median(held))
            attack = None
            if len(values) >= 25:
                attack = float(100 * (np.median(values[:8]) - center))
            notes.append({
                "start": round((begin + left) * HOP_SECONDS, 3),
                "end": round(min((begin + right) * HOP_SECONDS, duration), 3),
                "midi": round(center, 4), "note": midi_to_note(center),
                "median_hz": round(midi_to_hz(center), 3),
                "stability_cents": round(float(100 * np.median(np.abs(held - center))), 1),
                "attack_cents": round(attack, 1) if attack is not None else None,
            })
    return notes


def analyze_pitch(
    path: Path, *, fmin: float = 55.0, fmax: float = 1100.0,
    device: str = "cpu", progress: Progress | None = None,
) -> dict:
    """Return a JSON-safe F0 trace and descriptive note regions for one voice.

    Uploads should be dry solo vocals. Polyphonic music, unison singers and
    separation bleed can confuse a monophonic estimator; no tracker can promise
    perfect recovery. Failures are surfaced rather than replaced by a different
    algorithm with silently different behavior.
    """
    if not (math.isfinite(fmin) and math.isfinite(fmax) and 32.8 <= fmin < fmax <= 1975):
        raise ValueError("Choose a pitch range between 32.8 Hz and 1975 Hz, with the high note above the low note.")
    if np.sum((_BIN_HZ >= fmin) & (_BIN_HZ <= fmax)) < 5:
        raise ValueError("Choose a pitch range at least one semitone wide.")
    if progress:
        progress(0.02, "Preparing your recording")
    audio = _load_audio(Path(path))
    duration = len(audio) / SAMPLE_RATE
    activations = _infer_activations(audio, device, progress)
    frame_count = min(len(activations), math.ceil(len(audio) / HOP_LENGTH))
    activations = activations[:frame_count]
    if frame_count == 0 or activations.shape != (frame_count, 360) or not np.isfinite(activations).all():
        raise RuntimeError("The pitch model returned an invalid result. Please try the recording again.")
    if progress:
        progress(0.84, "Cleaning harmonic glitches and preserving note changes")
    rms = _rms_db(audio, frame_count)
    midi, raw_hz, confidence = _track_from_activations(activations, rms, fmin, fmax, audio=audio)
    voiced = np.isfinite(midi)
    notes = _notes(midi, duration)
    frames = []
    for index in range(frame_count):
        frames.append({
            "time": round(index * HOP_SECONDS, 3),
            "hz": round(midi_to_hz(float(midi[index])), 3) if voiced[index] else None,
            "raw_hz": round(float(raw_hz[index]), 3) if np.isfinite(raw_hz[index]) else None,
            "midi": round(float(midi[index]), 4) if voiced[index] else None,
            "confidence": round(float(confidence[index]), 4),
        })
    warnings = []
    if np.mean(np.abs(audio) >= 0.995) > 0.005:
        warnings.append("The recording is clipping. Move slightly farther from the microphone for a clearer trace.")
    voiced_seconds = min(float(voiced.sum()) * HOP_SECONDS, duration)
    if voiced_seconds < 0.5:
        warnings.append("Very little clear singing was detected. Try a dry solo recording with a longer held note.")
    elif float(voiced.mean()) < 0.35:
        warnings.append("Many regions were silent or uncertain. Gaps are left open instead of inventing a pitch.")
    if np.any(voiced) and float(np.median(confidence[voiced])) < 0.5:
        warnings.append("Tracking confidence is low. A quieter room or isolated vocal will give more reliable feedback.")
    if np.any(voiced):
        edge = (midi[voiced] < hz_to_midi(fmin) + 0.4) | (midi[voiced] > hz_to_midi(fmax) - 0.4)
        if np.mean(edge) > 0.03:
            warnings.append("Some notes reach the selected pitch-range boundary. A wider range may recover them.")
    if progress:
        progress(1.0, "Your pitch trace is ready")
    return {
        "duration_seconds": round(duration, 4), "hop_seconds": HOP_SECONDS,
        "frames": frames, "notes": notes,
        "summary": {
            "voiced_seconds": round(voiced_seconds, 3),
            "voiced_percent": round(100 * voiced_seconds / duration, 1),
            "median_confidence": round(float(np.median(confidence[voiced])), 4) if np.any(voiced) else 0,
            "lowest_note": midi_to_note(float(np.percentile(midi[voiced], 2))) if np.any(voiced) else None,
            "highest_note": midi_to_note(float(np.percentile(midi[voiced], 98))) if np.any(voiced) else None,
            "note_count": len(notes),
        },
        "warnings": warnings, "engine": ENGINE,
    }
