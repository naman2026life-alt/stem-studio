from __future__ import annotations

import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from pydub import AudioSegment

SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"}


def validate_audio(path: str | Path) -> Path:
    audio_path = Path(path)
    if not audio_path.exists():
        raise ValueError("Audio file could not be found.")
    if audio_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported format: {audio_path.suffix}. Use MP3, WAV, M4A, FLAC, AAC, or OGG.")
    return audio_path


def run_demucs(source: str | Path, output_root: str | Path, model: str = "htdemucs") -> dict[str, Path]:
    source = validate_audio(source)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "demucs", "--two-stems", "vocals", "-n", model, "-o", str(output_root), str(source)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Unknown Demucs error"
        raise RuntimeError(f"Separation failed: {detail}")

    stem_dir = output_root / model / source.stem
    vocals = stem_dir / "vocals.wav"
    no_vocals = stem_dir / "no_vocals.wav"
    if not vocals.exists() or not no_vocals.exists():
        raise RuntimeError("Demucs finished, but expected output files were not created.")

    # A second pass produces a genuine drums stem; Demucs reuses its downloaded model.
    command[3:5] = ["--two-stems", "drums"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Unknown Demucs error"
        raise RuntimeError(f"Drums separation failed: {detail}")
    drums = stem_dir / "drums.wav"
    if not drums.exists():
        raise RuntimeError("Demucs finished, but the drums output was not created.")
    return {"vocals": vocals, "drums": drums, "instrumental": no_vocals}


def _trim(segment: AudioSegment, start_seconds: float, end_seconds: float) -> AudioSegment:
    start_ms = max(0, round(start_seconds * 1000))
    end_ms = len(segment) if end_seconds <= 0 else min(len(segment), round(end_seconds * 1000))
    if end_ms <= start_ms:
        raise ValueError("Vocal trim end must be after trim start.")
    return segment[start_ms:end_ms]


def mix_tracks(
    instrumental_path: str | Path,
    vocal_path: str | Path,
    output_dir: str | Path,
    offset_ms: int = 0,
    trim_start_seconds: float = 0,
    trim_end_seconds: float = 0,
    vocal_gain_db: float = 0,
    instrumental_gain_db: float = 0,
) -> tuple[Path, Path]:
    instrumental = AudioSegment.from_file(validate_audio(instrumental_path)) + instrumental_gain_db
    vocal = AudioSegment.from_file(validate_audio(vocal_path)) + vocal_gain_db
    vocal = _trim(vocal, trim_start_seconds, trim_end_seconds)

    if offset_ms < 0:
        vocal = vocal[min(len(vocal), -offset_ms):]
        offset_ms = 0
    duration = max(len(instrumental), offset_ms + len(vocal))
    bed = instrumental + AudioSegment.silent(duration=max(0, duration - len(instrumental)))
    mixed = bed.overlay(vocal, position=offset_ms)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    wav_path = output_dir / f"mix-{token}.wav"
    mp3_path = output_dir / f"mix-{token}.mp3"
    mixed.export(wav_path, format="wav")
    mixed.export(mp3_path, format="mp3", bitrate="320k")
    return wav_path, mp3_path


def session_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="stem-studio-"))
