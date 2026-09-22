import json

import numpy as np
import pytest

from stem_studio.pitch_compare import compare_pitch


def contour(notes=(60, 64, 62, 67, 65, 69), delay=0, detune=0, duration=None):
    total = duration or (len(notes) * 0.6 + delay + 0.3)
    times = np.arange(0, total, 0.01)
    frames = []
    for time in times:
        index = int((time - delay) // 0.6)
        midi = notes[index] + detune if time >= delay and 0 <= index < len(notes) else None
        frames.append({"time": round(float(time), 4), "hz": 440 * 2 ** ((midi - 69) / 12) if midi is not None else None})
    return {"duration_seconds": total, "frames": frames, "notes": [
        {"start": delay + i * 0.6, "end": delay + (i + 1) * 0.6, "note": f"Note {i}"}
        for i in range(len(notes))
    ]}


def test_exact_phrase_alignment_and_no_transposition():
    result = compare_pitch(contour(delay=0.48), contour())
    assert result["reliable"]
    assert result["alignment"]["offset_seconds"] == pytest.approx(-0.48, abs=0.04)
    assert result["score"] >= 97
    assert result["metrics"]["median_absolute_cents"] == 0
    json.dumps(result, allow_nan=False)


def test_octave_error_is_not_silently_forgiven():
    wrong = contour(detune=12)
    result = compare_pitch(wrong, contour())
    assert result["reliable"]
    assert result["score"] == 0
    assert result["metrics"]["median_absolute_cents"] == pytest.approx(1200)
    adjusted = compare_pitch(wrong, contour(), transpose_semitones=-12)
    assert adjusted["score"] >= 97


def test_missing_notes_lower_score_not_just_precision():
    result = compare_pitch(contour(notes=(60, 64, 62), duration=3.9), contour(), alignment="manual")
    assert result["reliable"]
    assert 45 <= result["score"] <= 55
    assert 45 <= result["metrics"]["reference_coverage_percent"] <= 55


def test_steady_tone_alignment_requests_manual_confirmation():
    same = contour(notes=(60, 60, 60, 60))
    auto = compare_pitch(same, same)
    assert auto["score"] is None
    assert auto["alignment"]["ambiguous"]
    assert compare_pitch(same, same, alignment="manual")["score"] == 100


def test_noise_silence_and_short_overlap_are_not_scored():
    silence = contour()
    for frame in silence["frames"]:
        frame["hz"] = None
    result = compare_pitch(silence, contour(), alignment="manual")
    assert result["score"] is None
    assert result["metrics"]["matched_voiced_seconds"] == 0
    assert all(p["take_hz"] is None for p in result["points"])
    assert compare_pitch(contour(), contour(), alignment="manual", offset_seconds=3.5)["score"] is None


def test_flat_bias_is_not_mistaken_for_scooped_attacks():
    take = contour(detune=-0.4)
    result = compare_pitch(take, contour(), alignment="manual")
    assert result["metrics"]["bias_cents"] == pytest.approx(-40)
    assert any("flat" in f["title"] for f in result["feedback"])
    assert not any("approach notes" in f["title"] for f in result["feedback"])


def test_scooped_attacks_are_distinct_from_general_tuning():
    take = contour()
    for frame in take["frames"]:
        phase = frame["time"] % 0.6
        if frame["hz"] and phase < 0.15:
            frame["hz"] *= 2 ** (-80 * (1 - phase / 0.15) / 1200)
    result = compare_pitch(take, contour(), alignment="manual")
    assert abs(result["metrics"]["bias_cents"]) < 5
    assert any("approach notes from below" in f["title"] for f in result["feedback"])


def test_real_wrong_notes_are_not_time_warped_away():
    wrong = contour(notes=(60, 64, 68, 67, 65, 69))
    result = compare_pitch(wrong, contour(), alignment="manual")
    assert result["reliable"]
    assert 75 <= result["score"] <= 86
    assert result["note_feedback"][2]["median_error_cents"] == pytest.approx(600)


@pytest.mark.parametrize("option", [dict(offset_seconds=float("nan")), dict(transpose_semitones=25), dict(alignment="warp")])
def test_invalid_controls_rejected(option):
    with pytest.raises(ValueError):
        compare_pitch(contour(), contour(), **option)


def test_silence_gaps_are_never_interpolated():
    take = contour()
    for frame in take["frames"]:
        if 1 < frame["time"] < 1.4:
            frame["hz"] = None
    result = compare_pitch(take, contour(), alignment="manual")
    assert all(p["take_hz"] is None for p in result["points"] if 1.03 < p["time"] < 1.37)


def test_reference_lead_in_gives_positive_offset():
    result = compare_pitch(contour(), contour(delay=0.72))
    assert result["reliable"]
    assert result["alignment"]["offset_seconds"] == pytest.approx(0.72, abs=0.04)
    assert result["score"] >= 97


def test_vibrato_is_not_flattened_or_called_wrong_note():
    take = contour()
    for frame in take["frames"]:
        if frame["hz"] is not None:
            frame["hz"] *= 2 ** (20 * np.sin(2 * np.pi * 5 * frame["time"]) / 1200)
    result = compare_pitch(take, contour(), alignment="manual")
    assert result["score"] >= 98
    audible = [p["cents_error"] for p in result["points"] if p["cents_error"] is not None]
    assert max(audible) > 15
    assert min(audible) < -15


def test_repeated_phrase_is_ambiguous_in_long_reference():
    take = contour()
    repeated = contour(notes=(60, 64, 62, 67, 65, 69) * 2)
    result = compare_pitch(take, repeated)
    assert result["alignment"]["ambiguous"]
    assert result["score"] is None


def test_too_short_coverage_cannot_earn_perfect_score():
    reference = contour(notes=(60, 64, 62, 67, 65, 69) * 4)
    result = compare_pitch(contour(), reference, alignment="manual")
    assert result["score"] is None
    assert result["metrics"]["reference_coverage_percent"] < 30


def test_linear_glide_cannot_distinguish_timing_from_key_change():
    def glide(delay):
        return {"duration_seconds": 4.0, "frames": [
            {"time": round(float(t), 3), "hz": 440 * 2 ** ((60 + 2 * (t - delay) - 69) / 12)}
            for t in np.arange(0, 4, 0.01)
        ], "notes": []}
    result = compare_pitch(glide(0.5), glide(0))
    assert result["alignment"]["ambiguous"]
    assert result["score"] is None
    assert result["alignment"]["confidence"] == "low"


def test_sparse_frames_do_not_invent_voice_between_samples():
    sparse = {"duration_seconds": 5, "frames": [{"time": 0, "hz": 440}, {"time": 4.99, "hz": 440}]}
    result = compare_pitch(sparse, sparse, alignment="manual")
    assert result["score"] is None
    assert result["metrics"]["matched_voiced_seconds"] < 0.15


def test_low_model_evidence_cannot_earn_a_reliable_score():
    take = contour()
    take["summary"] = {"median_confidence": 0.22}
    result = compare_pitch(take, contour(), alignment="manual")
    assert result["score"] is None
    assert "uncertain" in result["unscored_reason"]


@pytest.mark.parametrize("frame", [None, {"hz": 440}, {"time": "oops", "hz": 440}, {"time": 0, "hz": []}])
def test_malformed_frames_raise_safe_validation_error(frame):
    bad = contour()
    bad["frames"] = [frame]
    with pytest.raises(ValueError):
        compare_pitch(bad, contour(), alignment="manual")


@pytest.mark.parametrize("metadata", [
    {"summary": None}, {"summary": {"median_confidence": "invalid"}},
    {"summary": {"median_confidence": float("nan")}},
    {"summary": {"median_confidence": 1.1}}, {"notes": None},
    {"notes": [None]}, {"notes": [{"start": 0, "end": float("nan")}]},
])
def test_malformed_metadata_raises_safe_validation_error(metadata):
    bad = contour()
    bad.update(metadata)
    with pytest.raises(ValueError):
        compare_pitch(contour(), bad, alignment="manual")
