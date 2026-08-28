import io
from pathlib import Path

import pytest
import yt_dlp
from pydub.generators import Sine
from yt_dlp.utils import DownloadError

from stem_studio.youtube import (
    YouTubeHostedBlockError,
    YouTubeImportError,
    YouTubeUrlError,
    canonicalize_youtube_url,
    import_youtube_audio,
)


@pytest.mark.parametrize(
    ("url", "video_id"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ&feature=share", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ],
)
def test_canonicalize_youtube_video_urls(url: str, video_id: str):
    assert canonicalize_youtube_url(url) == f"https://www.youtube.com/watch?v={video_id}"


@pytest.mark.parametrize(
    "url",
    [
        "http://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com:443/watch?v=dQw4w9WgXcQ",
        "https://user@youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
        "https://127.0.0.1/watch?v=dQw4w9WgXcQ",
        "file:///etc/passwd",
        "https://youtube.com/playlist?list=PL123",
        "https://youtube.com/watch?v=too-short",
        "https://youtu.be/dQw4w9WgXcQ/extra",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&feature=" + "x" * 2048,
    ],
)
def test_rejects_noncanonical_or_unsafe_youtube_urls(url: str):
    with pytest.raises(YouTubeUrlError):
        canonicalize_youtube_url(url)


def test_import_youtube_audio_uses_bounded_single_video_options(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url: str, download: bool):
            assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            assert download
            info = {"id": "dQw4w9WgXcQ", "title": "A / useful: title", "duration": 1.0}
            assert captured["match_filter"](info, incomplete=False) is None
            captured["progress_hooks"][0]({"downloaded_bytes": 1024})
            audio = io.BytesIO()
            Sine(440).to_audio_segment(duration=1000).export(audio, format="mp3", bitrate="192k")
            (tmp_path / "source.mp3").write_bytes(audio.getvalue())
            return info

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    result = import_youtube_audio("https://youtu.be/dQw4w9WgXcQ", tmp_path)

    assert result.path == tmp_path / "source.mp3"
    assert result.download_name == "A useful title-youtube.mp3"
    assert result.duration_seconds == pytest.approx(1.0, abs=0.1)
    assert captured["noplaylist"] is True
    assert captured["playlist_items"] == "1"
    assert "max_downloads" not in captured
    assert captured["outtmpl"] == str(tmp_path / "source.%(ext)s")
    assert captured["js_runtimes"] == {"deno": {}}
    assert captured["postprocessors"][0]["preferredquality"] == "192"


def test_import_rejects_live_or_long_video_before_accepting_output(monkeypatch, tmp_path: Path):
    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url: str, download: bool):
            info = {"id": "dQw4w9WgXcQ", "title": "Live", "duration": 5, "is_live": True}
            self.options["match_filter"](info, incomplete=False)
            return info

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    with pytest.raises(YouTubeImportError, match="Live and upcoming"):
        import_youtube_audio("https://youtu.be/dQw4w9WgXcQ", tmp_path)


def test_import_redacts_raw_ytdlp_error_and_logs_nothing(monkeypatch, tmp_path: Path, capsys):
    class FailingYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url: str, download: bool):
            self.options["logger"].error("internal-secret-and-full-upstream-response")
            raise DownloadError("internal-secret-and-full-upstream-response")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FailingYoutubeDL)
    with pytest.raises(YouTubeImportError) as error:
        import_youtube_audio("https://youtu.be/dQw4w9WgXcQ", tmp_path)
    assert "internal-secret" not in str(error.value)
    captured = capsys.readouterr()
    assert "internal-secret" not in captured.out
    assert "internal-secret" not in captured.err


def test_import_retries_cloud_block_with_embedded_player_client(monkeypatch, tmp_path: Path):
    attempts: list[dict[str, object]] = []

    class RetryYoutubeDL:
        def __init__(self, options):
            self.options = options
            attempts.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url: str, download: bool):
            if len(attempts) == 1:
                (tmp_path / "source.partial").write_bytes(b"stale first attempt")
                raise DownloadError("HTTP Error 403: sign in to confirm you're not a bot")

            assert not (tmp_path / "source.partial").exists()
            audio = io.BytesIO()
            Sine(440).to_audio_segment(duration=100).export(audio, format="mp3", bitrate="192k")
            (tmp_path / "source.mp3").write_bytes(audio.getvalue())
            return {"id": "dQw4w9WgXcQ", "title": "Recovered", "duration": 0.1}

    monkeypatch.setattr(yt_dlp, "YoutubeDL", RetryYoutubeDL)

    result = import_youtube_audio("https://youtu.be/dQw4w9WgXcQ", tmp_path)

    assert result.title == "Recovered"
    assert len(attempts) == 2
    assert "extractor_args" not in attempts[0]
    assert attempts[1]["extractor_args"] == {"youtube": {"player_client": ["web_embedded"]}}
    assert all(attempt["source_address"] == "0.0.0.0" for attempt in attempts)


@pytest.mark.parametrize(
    "raw_error",
    [
        "HTTP Error 403: Forbidden",
        "HTTP Error 429: Too Many Requests",
        "Sign in to confirm you're not a bot",
    ],
)
def test_import_classifies_repeated_cloud_rejection_for_home_helper(
    monkeypatch,
    tmp_path: Path,
    raw_error: str,
):
    attempts = 0

    class BlockedYoutubeDL:
        def __init__(self, options):
            nonlocal attempts
            attempts += 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url: str, download: bool):
            raise DownloadError(raw_error)

    monkeypatch.setattr(yt_dlp, "YoutubeDL", BlockedYoutubeDL)

    with pytest.raises(YouTubeHostedBlockError) as error:
        import_youtube_audio("https://youtu.be/dQw4w9WgXcQ", tmp_path)

    assert attempts == 2
    assert error.value.code == "host_blocked"
    assert raw_error not in str(error.value)


def test_import_preserves_cloud_block_classification_if_embedded_retry_is_generic(
    monkeypatch,
    tmp_path: Path,
):
    attempts = 0

    class BlockedThenGenericYoutubeDL:
        def __init__(self, options):
            nonlocal attempts
            attempts += 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url: str, download: bool):
            if attempts == 1:
                raise DownloadError("HTTP Error 403: Forbidden")
            raise DownloadError("This content is not available on this app")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", BlockedThenGenericYoutubeDL)

    with pytest.raises(YouTubeHostedBlockError):
        import_youtube_audio("https://youtu.be/dQw4w9WgXcQ", tmp_path)

    assert attempts == 2


def test_import_requires_exact_expected_output(monkeypatch, tmp_path: Path):
    class WrongOutputYoutubeDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url: str, download: bool):
            (tmp_path / "attacker-controlled.mp3").write_bytes(b"not audio")
            return {"id": "dQw4w9WgXcQ", "title": "Song", "duration": 1}

    monkeypatch.setattr(yt_dlp, "YoutubeDL", WrongOutputYoutubeDL)
    with pytest.raises(YouTubeImportError, match="usable audio file"):
        import_youtube_audio("https://youtu.be/dQw4w9WgXcQ", tmp_path)
