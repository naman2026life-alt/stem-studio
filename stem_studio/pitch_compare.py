"""Compare measured singing contours without quantizing or silently changing key.

Alignment estimates a single time translation. It never stretches time or fixes
octaves; those choices would hide precisely the mistakes a learner wants to see.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

MAX_DURATION = 90.1
GRID_SECONDS = 0.02


def _contour(analysis: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, float]:
    if not isinstance(analysis, dict):
        raise ValueError("This pitch analysis is incomplete. Analyze the recording again.")
    try:
        duration = float(analysis.get("duration_seconds", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("This pitch analysis has an invalid duration.") from exc
    if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
        raise ValueError("Choose a phrase of 90 seconds or less to compare.")
    frames = analysis.get("frames")
    if not isinstance(frames, list) or not frames or len(frames) > 20000:
        raise ValueError("This pitch analysis is incomplete. Analyze the recording again.")
    times, pitches = [], []
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("This pitch analysis has an invalid frame.")
        try:
            timestamp = float(frame["time"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("This pitch analysis has an invalid timeline.") from exc
        hz = frame.get("hz")
        if not math.isfinite(timestamp) or timestamp < 0 or timestamp > duration + 0.1:
            raise ValueError("This pitch analysis has an invalid timeline.")
        if hz is None:
            midi = np.nan
        else:
            try:
                hz = float(hz)
            except (TypeError, ValueError) as exc:
                raise ValueError("This pitch analysis has an invalid frequency.") from exc
            if not math.isfinite(hz) or not 20 <= hz <= 4000:
                raise ValueError("This pitch analysis has an invalid frequency.")
            midi = 69 + 12 * math.log2(hz / 440)
        times.append(timestamp)
        pitches.append(midi)
    time_array = np.asarray(times)
    if len(time_array) > 1 and np.any(np.diff(time_array) <= 0):
        raise ValueError("This pitch analysis has an invalid timeline.")
    return time_array, np.asarray(pitches), duration


def _sample(times: np.ndarray, pitches: np.ndarray, query: np.ndarray) -> np.ndarray:
    """Nearest-frame sampling keeps silence gaps and avoids interpolating notes."""
    indices = np.searchsorted(times, query)
    right = np.clip(indices, 0, len(times) - 1)
    left = np.clip(indices - 1, 0, len(times) - 1)
    nearest = np.where(np.abs(times[left] - query) <= np.abs(times[right] - query), left, right)
    result = pitches[nearest].copy()
    hop = float(np.median(np.diff(times))) if len(times) > 1 else 0.01
    result[np.abs(times[nearest] - query) > min(0.03, max(0.015, hop * 0.75))] = np.nan
    return result


def _alignment(
    take_times: np.ndarray,
    take_pitches: np.ndarray,
    take_duration: float,
    ref_times: np.ndarray,
    ref_pitches: np.ndarray,
    ref_duration: float,
) -> tuple[float, str, bool]:
    # 25 Hz makes a bounded exhaustive search inexpensive, even for 90 seconds.
    step = 0.04
    grid = np.arange(0, ref_duration, step)
    reference = _sample(ref_times, ref_pitches, grid)
    take_grid = np.arange(0, take_duration, step)
    take = _sample(take_times, take_pitches, take_grid)
    ref_voiced = np.isfinite(reference)
    take_voiced = np.isfinite(take)
    available = min(int(ref_voiced.sum()), int(take_voiced.sum()))
    if available < 25:
        return 0.0, "low", True

    def continuous_linear_glide(pitches: np.ndarray, times: np.ndarray) -> bool:
        voiced = np.isfinite(pitches)
        if voiced.mean() < 0.97:
            return False
        x, y = times[voiced], pitches[voiced]
        slope, intercept = np.polyfit(x, y, 1)
        residual = np.abs(y - (slope * x + intercept))
        return abs(slope) > 0.2 and float(np.percentile(residual, 90)) < 0.18

    if continuous_linear_glide(take, take_grid) and continuous_linear_glide(reference, grid):
        # On a continuous linear slide, a time shift is indistinguishable from
        # a key shift. Clip endpoint overlap must not manufacture certainty.
        return 0.0, "low", True

    # A steady note (or a simple continuous slide) does not identify a unique
    # start. Keep scoring disabled rather than presenting an arbitrary match.
    def objective(offset: float) -> float:
        query = grid - offset
        shifted = _sample(take_times, take_pitches, query)
        active = (query >= 0) & (query <= take_duration)
        voiced = np.isfinite(shifted)
        matched = ref_voiced & voiced
        count = int(matched.sum())
        if count < max(25, available * 0.45):
            return -1.0
        differences = shifted[matched] - reference[matched]
        # Ignore constant key difference only when locating the phrase; scoring
        # below uses the uncentered pitches, including every octave error.
        deviations = np.abs(differences - np.median(differences))
        melody = float(np.mean(np.exp(-deviations / 0.7)))
        union = int(((ref_voiced | voiced) & active).sum())
        voice_agreement = count / max(1, union)
        coverage = min(1.0, count / available)
        return (0.82 * melody + 0.18 * voice_agreement) * coverage**0.6

    offsets = np.arange(-take_duration + 1, ref_duration - 1 + step / 2, step)
    if not len(offsets):
        return 0.0, "low", True
    values = np.asarray([objective(float(offset)) for offset in offsets])
    best_index = int(np.argmax(values))
    best_offset = float(offsets[best_index])
    nearby = np.arange(best_offset - 0.05, best_offset + 0.051, 0.01)
    fine_values = np.asarray([objective(float(offset)) for offset in nearby])
    fine_index = int(np.argmax(fine_values))
    best_offset = float(nearby[fine_index])
    best_value = float(fine_values[fine_index])
    other = values[np.abs(offsets - best_offset) >= 0.35]
    runner_up = float(np.max(other)) if len(other) else -1.0
    margin = best_value - runner_up
    take_spread = float(np.nanpercentile(take, 90) - np.nanpercentile(take, 10))
    ambiguous = best_value < 0.65 or margin < 0.018 or take_spread < 0.65
    confidence = "low" if ambiguous else "high" if margin >= 0.045 and best_value >= 0.82 else "medium"
    return round(best_offset, 3), confidence, ambiguous


def _number(value: float | np.floating | None, digits: int = 1) -> float | None:
    return round(float(value), digits) if value is not None and math.isfinite(float(value)) else None


def _hz(midi: float) -> float | None:
    return round(440 * 2 ** ((float(midi) - 69) / 12), 3) if np.isfinite(midi) else None


def _confidence(analysis: dict[str, Any]) -> float:
    summary = analysis.get("summary", {})
    if not isinstance(summary, dict):
        raise ValueError("This pitch analysis has invalid confidence data. Analyze it again.")
    try:
        value = float(summary.get("median_confidence", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("This pitch analysis has invalid confidence data. Analyze it again.") from exc
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("This pitch analysis has invalid confidence data. Analyze it again.")
    return value


def compare_pitch(
    take: dict[str, Any],
    reference: dict[str, Any],
    *,
    alignment: str = "auto",
    offset_seconds: float = 0.0,
    transpose_semitones: float = 0.0,
) -> dict[str, Any]:
    """Return a pitch-only practice score and an overlay on reference time.

    Aligned time = take time + offset_seconds. Transposition changes the take
    only, explicitly; auto alignment does not forgive a wrong key or octave.
    Missing reference notes lower the score, while silence is never a note.
    """
    if alignment not in {"auto", "manual"}:
        raise ValueError("Choose automatic or manual alignment.")
    if not math.isfinite(offset_seconds) or abs(offset_seconds) > 90:
        raise ValueError("Keep the alignment offset between -90 and 90 seconds.")
    if not math.isfinite(transpose_semitones) or abs(transpose_semitones) > 24:
        raise ValueError("Keep the key adjustment within two octaves.")
    take_times, take_pitches, take_duration = _contour(take)
    ref_times, ref_pitches, ref_duration = _contour(reference)
    minimum_confidence = min(_confidence(take), _confidence(reference))
    take_pitches = take_pitches + transpose_semitones
    if alignment == "auto":
        offset_seconds, confidence, ambiguous = _alignment(
            take_times, take_pitches, take_duration, ref_times, ref_pitches, ref_duration,
        )
    else:
        confidence, ambiguous = "high", False

    grid = np.arange(0, ref_duration, GRID_SECONDS)
    ref_values = _sample(ref_times, ref_pitches, grid)
    take_values = _sample(take_times, take_pitches, grid - offset_seconds)
    valid_reference = np.isfinite(ref_values)
    matched = valid_reference & np.isfinite(take_values)
    errors = (take_values - ref_values) * 100
    matched_seconds = float(matched.sum() * GRID_SECONDS)
    ref_seconds = float(valid_reference.sum() * GRID_SECONDS)
    coverage = 100 * matched_seconds / ref_seconds if ref_seconds else 0.0
    overlap = max(0.0, min(ref_duration, take_duration + offset_seconds) - max(0.0, offset_seconds))
    signed = errors[matched]
    absolute = np.abs(signed)
    median_abs = float(np.median(absolute)) if len(absolute) else None
    bias = float(np.median(signed)) if len(signed) else None
    within = float(np.mean(absolute <= 50) * 100) if len(absolute) else None
    reason = None
    if ref_seconds < 1:
        reason = "The reference needs at least one second of clear singing. Choose a vocal phrase."
    elif matched_seconds < 1:
        reason = "There is less than one second of matching voiced audio. Check the clips and their timing."
    elif coverage < 30:
        reason = "Too little of the reference has a matching vocal. Choose the same phrase and check alignment."
    elif minimum_confidence < 0.5:
        reason = "Pitch tracking is uncertain in one recording. Use a clearer solo vocal before relying on a score."
    elif ambiguous:
        reason = "The start could not be aligned confidently. Adjust the offset and use manual alignment to score."

    # Piecewise tolerance has a forgiving 25-cent centre, falls to 50 points
    # at one semitone, and to zero at three semitones. Missing notes count zero.
    points_per_frame = np.interp(absolute, [0, 25, 100, 300], [100, 100, 50, 0])
    score = round(float(points_per_frame.sum() / max(1, valid_reference.sum()))) if reason is None else None

    note_feedback: list[dict[str, Any]] = []
    note_regions = reference.get("notes", [])
    if not isinstance(note_regions, list):
        raise ValueError("This pitch analysis has invalid note regions. Analyze it again.")
    for note in note_regions[:400]:
        try:
            start, end = float(note["start"]), float(note["end"])
            if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= ref_duration + 0.1:
                raise ValueError("Invalid note times")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("This pitch analysis has invalid note regions. Analyze it again.") from exc
        region = (grid >= start) & (grid < end) & valid_reference
        paired = region & matched
        count = int(paired.sum())
        note_coverage = 100 * count / max(1, int(region.sum()))
        note_errors = errors[paired]
        # First 120 ms compares the attack against the reference's actual
        # attack, not a snapped note target; legitimate scoops remain valid.
        attack = paired & (grid < start + min(0.12, (end - start) / 3))
        note_feedback.append({
            "start": round(start, 3), "end": round(end, 3), "note": str(note.get("note", "")),
            "median_error_cents": _number(np.median(note_errors)) if count >= 3 else None,
            "attack_error_cents": _number(np.median(errors[attack])) if int(attack.sum()) >= 3 else None,
            "coverage_percent": round(note_coverage, 1),
        })

    feedback: list[dict[str, str]] = []
    if reason:
        feedback.append({"kind": "info", "title": "Comparison needs a closer look", "detail": reason})
    elif within is not None:
        feedback.append({
            "kind": "strength" if within >= 80 else "practice",
            "title": "Pitch match",
            "detail": f"{within:.0f}% of the compared singing is within half a semitone of the reference.",
        })
        if bias is not None and abs(bias) >= 20:
            direction = "sharp" if bias > 0 else "flat"
            feedback.append({
                "kind": "practice", "title": f"You tend to sing {direction}",
                "detail": f"Your median pitch is {abs(bias):.0f} cents {direction}. A semitone is 100 cents.",
            })
        if coverage < 85:
            feedback.append({
                "kind": "info", "title": "Some reference notes are missing",
                "detail": f"Clear singing covers {coverage:.0f}% of the reference vocal. Missing notes lower the score; check timing and recording level too.",
            })
        # Separate an entry habit from singing the entire phrase flat/sharp.
        # A uniform -40-cent take should receive general tuning feedback, not
        # a claim that its attacks scoop below its otherwise steady pitch.
        attacks = [n["attack_error_cents"] - n["median_error_cents"] for n in note_feedback
                   if n["attack_error_cents"] is not None and n["median_error_cents"] is not None
                   and n["coverage_percent"] >= 60]
        if len(attacks) >= 3:
            attack_array = np.asarray(attacks)
            typical = float(np.median(attack_array))
            direction_count = np.mean(attack_array < -25) if typical < 0 else np.mean(attack_array > 25)
            if abs(typical) >= 25 and direction_count >= 0.65:
                direction = "below" if typical < 0 else "above"
                feedback.append({
                    "kind": "practice", "title": f"You approach notes from {direction}",
                    "detail": f"Across {len(attacks)} note entries, the first 120 ms sits about {abs(typical):.0f} cents {direction} your later pitch relative to the reference. Try a slower phrase and listen to each entry; a scoop can also be intentional.",
                })
    if transpose_semitones:
        feedback.append({"kind": "info", "title": "Key adjustment applied", "detail": f"Your take is compared after a {transpose_semitones:+g}-semitone adjustment. The recording itself is unchanged."})

    return {
        "score": score, "reliable": reason is None, "unscored_reason": reason,
        "alignment": {"mode": alignment, "offset_seconds": round(offset_seconds, 3), "confidence": confidence, "ambiguous": ambiguous},
        "transpose_semitones": transpose_semitones,
        "metrics": {
            "median_absolute_cents": _number(median_abs), "bias_cents": _number(bias),
            "within_50_cents_percent": _number(within), "reference_coverage_percent": round(coverage, 1),
            "overlap_seconds": round(overlap, 3), "matched_voiced_seconds": round(matched_seconds, 3),
        },
        "points": [{"time": round(float(t), 3), "reference_hz": _hz(r), "take_hz": _hz(v),
                    "cents_error": _number(e) if ok else None}
                   for t, r, v, e, ok in zip(grid, ref_values, take_values, errors, matched, strict=True)],
        "note_feedback": note_feedback, "feedback": feedback,
    }
