from __future__ import annotations

import math
import os
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Sequence
from pathlib import Path

from pydub import AudioSegment

SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".webm"}
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
SUPPORTED_MEDIA_EXTENSIONS = SUPPORTED_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS


def validate_audio(path: str | Path) -> Path:
    audio_path = Path(path)
    if not audio_path.exists():
        raise ValueError("Audio file could not be found.")
    if audio_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported format: {audio_path.suffix}. Use MP3, WAV, M4A, FLAC, AAC, OGG, or WEBM audio.")
    return audio_path


def validate_media(path: str | Path) -> Path:
    media_path = Path(path)
    if not media_path.exists():
        raise ValueError("Media file could not be found.")
    if media_path.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
        raise ValueError(
            f"Unsupported format: {media_path.suffix}. Use a common audio file or MP4, MOV, M4V, MKV, WEBM, or AVI."
        )
    return media_path


def media_kind(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in SUPPORTED_EXTENSIONS:
        return "audio"
    if suffix in SUPPORTED_VIDEO_EXTENSIONS:
        return "video"
    raise ValueError(f"Unsupported format: {suffix or 'unknown'}.")


def _command_error(result: subprocess.CompletedProcess[str], fallback: str) -> RuntimeError:
    detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else fallback
    return RuntimeError(detail)


def probe_duration_seconds(path: str | Path) -> float:
    media_path = validate_media(path)
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(media_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise _command_error(result, "Could not read the media duration.")
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("Could not read the media duration.") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("The media file has no usable duration.")
    return duration


def extract_audio_to_mp3(source: str | Path, output_path: str | Path) -> Path:
    source_path = validate_media(source)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(source_path),
            "-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", "320k", str(destination),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        destination.unlink(missing_ok=True)
        raise _command_error(result, "Could not extract an audio track from this video.")
    return destination


def transcode_audio_to_mp3(
    source: str | Path,
    output_path: str | Path,
    bitrate: str = "192k",
) -> Path:
    """Create a compact, share-ready MP3 from an audio file."""
    source_path = validate_audio(source)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(source_path),
            "-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", bitrate, str(destination),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        destination.unlink(missing_ok=True)
        raise _command_error(result, "Could not prepare this audio for sharing.")
    return destination


def trim_and_merge_audio(
    source: str | Path,
    segments: Sequence[tuple[float, float | None]],
    output_path: str | Path,
) -> Path:
    source_path = validate_audio(source)
    if not segments:
        raise ValueError("Add at least one part to trim and merge.")
    audio = AudioSegment.from_file(source_path)
    duration_seconds = len(audio) / 1000
    merged = AudioSegment.empty()
    for index, (raw_start, raw_end) in enumerate(segments, start=1):
        start = float(raw_start)
        end = duration_seconds if raw_end is None else float(raw_end)
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError(f"Part {index} has an invalid time value.")
        start = min(duration_seconds, max(0, start))
        end = min(duration_seconds, max(0, end))
        if end <= start:
            raise ValueError(f"Part {index} must end after it starts.")
        merged += audio[round(start * 1000):round(end * 1000)]

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    merged.export(destination, format="mp3", bitrate="320k")
    return destination


def run_demucs(source: str | Path, output_root: str | Path, model: str = "htdemucs") -> dict[str, Path]:
    source = validate_audio(source)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    model_cache = Path(os.environ.get("TORCH_HOME", Path(__file__).resolve().parent.parent / ".model-cache"))
    model_cache.mkdir(parents=True, exist_ok=True)
    demucs_env = {**os.environ, "TORCH_HOME": str(model_cache)}
    command = [sys.executable, "-m", "demucs", "-n", model, "-o", str(output_root), str(source)]
    result = subprocess.run(command, capture_output=True, text=True, env=demucs_env)
    if result.returncode:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Unknown Demucs error"
        raise RuntimeError(f"Separation failed: {detail}")

    stem_dir = output_root / model / source.stem
    vocals = stem_dir / "vocals.wav"
    drums = stem_dir / "drums.wav"
    bass = stem_dir / "bass.wav"
    other = stem_dir / "other.wav"
    if not all(path.exists() for path in (vocals, drums, bass, other)):
        raise RuntimeError("Demucs finished, but expected output files were not created.")

    # A single four-stem pass is considerably faster than running two separate models.
    no_vocals = stem_dir / "no_vocals.wav"
    instrumental = AudioSegment.from_file(drums)
    instrumental = instrumental.overlay(AudioSegment.from_file(bass))
    instrumental = instrumental.overlay(AudioSegment.from_file(other))
    instrumental.export(no_vocals, format="wav")
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
