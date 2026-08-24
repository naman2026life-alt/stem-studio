import subprocess
from pathlib import Path

from pydub import AudioSegment
from pydub.generators import Sine

from stem_studio.audio import (
    extract_audio_to_mp3,
    mix_tracks,
    probe_duration_seconds,
    trim_and_merge_audio,
)


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


def test_trim_merge_preserves_order_and_clamps_to_duration(tmp_path: Path):
    source = tmp_path / "source.wav"
    output = tmp_path / "edited.mp3"
    Sine(330).to_audio_segment(duration=2000).export(source, format="wav")

    trim_and_merge_audio(source, [(0, 0.4), (1.2, 99)], output)

    assert output.exists()
    assert 1180 <= len(AudioSegment.from_file(output)) <= 1220


def test_extract_video_audio_to_mp3(tmp_path: Path):
    source = tmp_path / "clip.mp4"
    output = tmp_path / "clip.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-shortest",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )

    extract_audio_to_mp3(source, output)

    assert output.exists()
    assert 0.9 <= probe_duration_seconds(output) <= 1.1
