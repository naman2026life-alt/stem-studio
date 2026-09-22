import json
from pathlib import Path

import numpy as np
import pytest

from stem_studio import pitch


def activations_for(midi, strength=0.9):
    values = np.asarray(midi)
    bins_midi = 69 + 12 * np.log2(pitch._BIN_HZ / 440)
    return strength * np.exp(-((values[:, None] - bins_midi[None, :]) / 0.35) ** 2)


def test_frequency_helpers_roundtrip_and_labels():
    assert pitch.midi_to_note(69) == "A4"
    assert pitch.midi_to_note(60) == "C4"
    assert pitch.midi_to_note(61.1) == "C#4"
    assert pitch.hz_to_midi(pitch.midi_to_hz(60.25)) == pytest.approx(60.25)
    with pytest.raises(ValueError):
        pitch.hz_to_midi(0)
    with pytest.raises(ValueError):
        pitch.midi_to_note(float("nan"))


def test_decoder_preserves_sustained_octave_leaps_without_staircases():
    truth = np.repeat([57.0, 69.0, 57.0], 50)
    result, _, confidence = pitch._track_from_activations(activations_for(truth), np.full(150, -25), 55, 1100)
    assert np.max(np.abs(result - truth)) < 0.1
    assert np.min(confidence) > 0.75


def test_octave_glitch_removal_is_short_and_does_not_cross_breaths():
    values = np.full(130, 57.0)
    values[25:28] = 69
    values[35:38] += 12 * np.log2(3)
    values[55:75] = 69
    values[90:95] = np.nan
    values[95:] = 69
    clean = pitch._smooth_midi(values)
    assert np.all(clean[25:28] == 57)
    np.testing.assert_allclose(clean[35:38], 57)
    assert np.all(clean[55:75] == 69)
    assert np.isnan(clean[90:95]).all()
    assert np.all(clean[95:] == 69)


def test_vibrato_and_scooped_attack_remain_in_the_continuous_trace():
    time = np.arange(200) * 0.01
    values = 57 + 0.35 * np.sin(2 * np.pi * 5 * time)
    values[:20] -= np.linspace(1, 0, 20)
    clean = pitch._smooth_midi(values)
    assert clean[0] == values[0]
    assert np.ptp(clean[40:]) > 0.60
    assert np.max(np.abs(clean - values)) < 0.08


def test_quiet_voice_survives_but_silence_and_low_evidence_noise_are_gaps():
    activations = activations_for(np.full(200, 57.0))
    rms = np.full(200, -75.0)
    rms[:30] = -160
    # CREPE can be confident on digital silence: energy must override it.
    activations[80:110] *= 0.1
    result, raw, confidence = pitch._track_from_activations(activations, rms, 55, 1100)
    assert np.isnan(result[:30]).all()
    assert np.isnan(result[80:110]).all()
    assert np.isfinite(result[110:]).all()
    assert np.isnan(raw[:30]).all()
    assert (confidence[:30] == 0).all()


def test_very_brief_confidence_islands_are_not_invented_notes():
    activations = np.zeros((100, 360))
    activations[30:33] = activations_for(np.full(3, 57.0))
    result, _, _ = pitch._track_from_activations(activations, np.full(100, -20), 55, 1100)
    assert np.isnan(result).all()


def test_dc_offset_does_not_turn_silence_into_energy():
    rms = pitch._rms_db(np.full(16000, 0.04, dtype=np.float32), 100)
    assert np.max(rms) < -120


def harmonic_wave(frequency):
    phase = 2 * np.pi * np.cumsum(frequency) / 16000
    return (0.05 * np.sin(phase) + np.sin(2 * phase) + 0.25 * np.sin(3 * phase)).astype(np.float32)


def test_waveform_consensus_repairs_sustained_neural_octave_error():
    audio = harmonic_wave(np.full(16000, 220.0))
    corrected = pitch._harmonic_consensus(np.full(100, 69.0), audio, 55)
    assert np.max(np.abs(corrected[5:-5] - 57)) < 0.1


def test_waveform_consensus_preserves_real_sustained_octave_changes():
    expected = np.repeat([57.0, 69.0, 57.0], 50)
    frequency = 440 * 2 ** ((np.repeat(expected, 160) - 69) / 12)
    audio = harmonic_wave(frequency)
    corrected = pitch._harmonic_consensus(expected, audio, 55)
    for begin in (0, 50, 100):
        assert np.max(np.abs(corrected[begin + 5:begin + 45] - expected[begin + 5:begin + 45])) < 0.01


def test_waveform_consensus_preserves_vibrato_and_low_scoops():
    time = np.arange(16000) / 16000
    midi = 57 + 0.35 * np.sin(2 * np.pi * 5 * time)
    midi[:3200] -= np.linspace(1, 0, 3200)
    audio = harmonic_wave(440 * 2 ** ((midi - 69) / 12))
    expected = midi[::160]
    corrected = pitch._harmonic_consensus(expected, audio, 55)
    np.testing.assert_allclose(corrected, expected)


def test_nearby_notes_with_vibrato_are_distinct_summary_regions():
    time = np.arange(200) * 0.01
    midi = np.repeat([60, 62, 64, 65], 50) + 0.25 * np.sin(2 * np.pi * 5 * time)
    notes = pitch._notes(midi, 2.0)
    assert [n["note"] for n in notes] == ["C4", "D4", "E4", "F4"]
    assert all(n["end"] - n["start"] > 0.4 for n in notes)


def test_attack_feedback_is_relative_to_sustained_center():
    midi = np.full(100, 57.0)
    midi[:20] -= np.linspace(1, 0, 20)
    notes = pitch._notes(midi, 1.0)
    assert len(notes) == 1
    assert notes[0]["note"] == "A3"
    assert notes[0]["attack_cents"] < -60
    assert notes[0]["stability_cents"] < 1


def test_analyze_pitch_json_has_no_nan_and_clip_time_is_bounded(monkeypatch):
    audio = np.sin(np.arange(16000) * 2 * np.pi * 220 / 16000).astype(np.float32) * 0.2
    activations = activations_for(np.full(101, 57.0))
    activations[30:40] = 0
    monkeypatch.setattr(pitch, "_load_audio", lambda path: audio)
    monkeypatch.setattr(pitch, "_infer_activations", lambda *args: activations)
    result = pitch.analyze_pitch(Path("unused.wav"))
    json.dumps(result, allow_nan=False)
    assert len(result["frames"]) == 100
    assert result["frames"][-1]["time"] == 0.99
    assert all(frame["hz"] is None for frame in result["frames"][30:40])
    assert result["summary"]["voiced_seconds"] == pytest.approx(0.9)


def test_model_failure_is_not_hidden_behind_fallback(monkeypatch):
    monkeypatch.setattr(pitch, "_load_audio", lambda path: np.ones(16000, dtype=np.float32))

    def fail(*args):
        raise RuntimeError("model could not load")

    monkeypatch.setattr(pitch, "_infer_activations", fail)
    with pytest.raises(RuntimeError, match="model could not load"):
        pitch.analyze_pitch(Path("unused.wav"))


def test_invalid_range_is_rejected_before_loading():
    with pytest.raises(ValueError, match="pitch range"):
        pitch.analyze_pitch(Path("unused.wav"), fmin=440, fmax=220)
    with pytest.raises(ValueError, match="semitone"):
        pitch.analyze_pitch(Path("unused.wav"), fmin=440, fmax=441)
