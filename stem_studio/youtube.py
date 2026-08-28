from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from stem_studio.audio import probe_duration_seconds

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}
YOUTUBE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{11}")
MAX_YOUTUBE_DURATION_SECONDS = 20 * 60
MAX_YOUTUBE_SOURCE_BYTES = 50 * 1024 * 1024
MAX_YOUTUBE_MP3_BYTES = 40 * 1024 * 1024
MAX_YOUTUBE_URL_CHARS = 2048


class YouTubeImportError(RuntimeError):
    """A safe, user-facing YouTube import error."""


class YouTubeUrlError(YouTubeImportError):
    """The submitted URL is not a supported single-video YouTube URL."""


class _QuietYtDlpLogger:
    """Prevent upstream details and video identifiers from reaching service logs."""

    def debug(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


@dataclass(frozen=True)
class ImportedYouTubeAudio:
    path: Path
    title: str
    download_name: str
    duration_seconds: float


def canonicalize_youtube_url(raw_url: str) -> str:
    """Validate a YouTube video URL and reduce it to a single canonical video ID."""
    if len(raw_url) > MAX_YOUTUBE_URL_CHARS:
        raise YouTubeUrlError("Paste a normal YouTube video link.")
    candidate = raw_url.strip()
    if not candidate:
        raise YouTubeUrlError("Paste a YouTube video link first.")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise YouTubeUrlError("Paste a valid YouTube video link.") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise YouTubeUrlError("Use a secure https:// YouTube video link.")
    if parsed.username or parsed.password or port is not None:
        raise YouTubeUrlError("Paste a normal YouTube video link without a login or custom port.")

    host = parsed.hostname.lower()
    if host not in YOUTUBE_HOSTS:
        raise YouTubeUrlError("Only youtube.com and youtu.be video links are supported.")

    path_parts = [part for part in parsed.path.split("/") if part]
    video_id = ""
    if host == "youtu.be":
        if len(path_parts) == 1:
            video_id = path_parts[0]
    elif parsed.path.rstrip("/") == "/watch":
        values = parse_qs(parsed.query).get("v", [])
        if len(values) == 1:
            video_id = values[0]
    elif len(path_parts) == 2 and path_parts[0] in {"shorts", "embed"}:
        video_id = path_parts[1]

    if not YOUTUBE_ID_PATTERN.fullmatch(video_id):
        raise YouTubeUrlError("Paste a link to one YouTube video, not a playlist or channel.")
    return f"https://www.youtube.com/watch?v={video_id}"


def _safe_download_name(title: object) -> tuple[str, str]:
    value = unicodedata.normalize("NFKC", str(title or "YouTube audio"))
    value = "".join(" " if unicodedata.category(character).startswith("C") else character for character in value)
    value = re.sub(r"[\\/:*?\"<>|]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")[:96].strip(" .")
    safe_title = value or "YouTube audio"
    return safe_title, f"{safe_title}-youtube.mp3"


def _safe_upstream_message(raw_message: str) -> str:
    message = raw_message.lower()
    if any(term in message for term in ("sign in to confirm", "not a bot", "http error 403", "http error 429")):
        return (
            "YouTube blocked the hosted downloader for this video. "
            "Download it on your computer and upload the audio here instead."
        )
    if any(term in message for term in ("private video", "members-only", "age-restricted", "not available in your country")):
        return "This YouTube video is restricted or unavailable. Try a public video you are allowed to download."
    return "YouTube could not provide this video right now. Check that it is public and try again shortly."


def import_youtube_audio(
    raw_url: str,
    output_directory: str | Path,
    *,
    max_duration_seconds: int = MAX_YOUTUBE_DURATION_SECONDS,
    max_source_bytes: int = MAX_YOUTUBE_SOURCE_BYTES,
    max_mp3_bytes: int = MAX_YOUTUBE_MP3_BYTES,
) -> ImportedYouTubeAudio:
    """Download one public YouTube video's audio and create a bounded 192 kbps MP3."""
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    canonical_url = canonicalize_youtube_url(raw_url)
    expected_video_id = canonical_url.rsplit("=", 1)[-1]
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("source.*"):
        stale.unlink(missing_ok=True)
    output = directory / "source.mp3"
    validation_error: list[str] = []
    source_too_large = False

    def match_filter(info: dict[str, object], *, incomplete: bool = False) -> str | None:
        live_status = str(info.get("live_status") or "")
        if info.get("is_live") or live_status in {"is_live", "is_upcoming"}:
            validation_error[:] = ["Live and upcoming YouTube streams are not supported."]
            return validation_error[0]
        duration = info.get("duration")
        if duration is None and incomplete:
            return None
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(float(duration)):
            validation_error[:] = ["Could not read this video's duration. Try a normal public video."]
            return validation_error[0]
        if float(duration) <= 0:
            validation_error[:] = ["This YouTube video does not contain usable audio."]
            return validation_error[0]
        if float(duration) > max_duration_seconds:
            validation_error[:] = [f"Choose a YouTube video that is {max_duration_seconds // 60} minutes or shorter."]
            return validation_error[0]
        estimated_size = info.get("filesize") or info.get("filesize_approx")
        if isinstance(estimated_size, (int, float)) and estimated_size > max_source_bytes:
            validation_error[:] = ["This YouTube audio is too large to import safely."]
            return validation_error[0]
        return None

    def progress_hook(status: dict[str, object]) -> None:
        nonlocal source_too_large
        downloaded = status.get("downloaded_bytes")
        if isinstance(downloaded, (int, float)) and downloaded > max_source_bytes:
            source_too_large = True
            raise YouTubeImportError("This YouTube audio is too large to import safely.")

    options = {
        "format": "bestaudio/best",
        "outtmpl": str(directory / "source.%(ext)s"),
        "noplaylist": True,
        "playlist_items": "1",
        "max_filesize": max_source_bytes,
        "overwrites": True,
        "continuedl": False,
        "ignoreconfig": True,
        "cachedir": False,
        "quiet": True,
        "no_warnings": True,
        "logger": _QuietYtDlpLogger(),
        "socket_timeout": 15,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_retries": 1,
        "match_filter": match_filter,
        "progress_hooks": [progress_hook],
        "js_runtimes": {"deno": {}},
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    try:
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(canonical_url, download=True)
    except YouTubeImportError:
        raise
    except DownloadError as exc:
        if validation_error:
            raise YouTubeImportError(validation_error[0]) from exc
        if source_too_large:
            raise YouTubeImportError("This YouTube audio is too large to import safely.") from exc
        raise YouTubeImportError(_safe_upstream_message(str(exc))) from exc
    except Exception as exc:
        if validation_error:
            raise YouTubeImportError(validation_error[0]) from exc
        if source_too_large:
            raise YouTubeImportError("This YouTube audio is too large to import safely.") from exc
        raise YouTubeImportError("YouTube audio import failed. Please try again shortly.") from exc

    if validation_error:
        raise YouTubeImportError(validation_error[0])
    if not isinstance(info, dict) or str(info.get("id") or "") != expected_video_id:
        raise YouTubeImportError("YouTube did not return the requested single video.")
    if not output.is_file() or output.stat().st_size <= 0:
        raise YouTubeImportError("YouTube did not return a usable audio file.")
    if output.stat().st_size > max_mp3_bytes:
        output.unlink(missing_ok=True)
        raise YouTubeImportError("The finished YouTube MP3 is too large to use here.")

    try:
        duration = probe_duration_seconds(output)
    except (OSError, RuntimeError, ValueError) as exc:
        output.unlink(missing_ok=True)
        raise YouTubeImportError("The downloaded YouTube audio could not be verified.") from exc
    if duration > max_duration_seconds + 1:
        output.unlink(missing_ok=True)
        raise YouTubeImportError(f"Choose a YouTube video that is {max_duration_seconds // 60} minutes or shorter.")

    title, download_name = _safe_download_name(info.get("title"))
    return ImportedYouTubeAudio(output, title, download_name, duration)
