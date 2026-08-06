from pathlib import Path

from pydub import AudioSegment
from pydub.generators import Sine

from stem_studio.audio import mix_tracks


def test_mix_offset_trim_and_exports(tmp_path: Path):
    instrumental = tmp_path / "instrumental.wav"
    vocal = tmp_path / "vocal.wav"
    Sine(220).to_audio_segment(duration=2000).export(instrumental, format="wav")
    Sine(440).to_audio_segment(duration=1500).export(vocal, format="wav")

    wav, mp3 = mix_tracks(instrumental, vocal, tmp_path, offset_ms=500, trim_start_seconds=.25, trim_end_seconds=1.25)

    assert wav.exists() and mp3.exists()
    assert 1990 <= len(AudioSegment.from_file(wav)) <= 2010


def test_negative_offset_skips_vocal_lead_in(tmp_path: Path):
    instrumental = tmp_path / "instrumental.wav"
    vocal = tmp_path / "vocal.wav"
    AudioSegment.silent(duration=1000).export(instrumental, format="wav")
    Sine(440).to_audio_segment(duration=1000).export(vocal, format="wav")
    wav, _ = mix_tracks(instrumental, vocal, tmp_path, offset_ms=-250)
    assert len(AudioSegment.from_file(wav)) == 1000
