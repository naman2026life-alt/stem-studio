import time

from modal_app import YOUTUBE_IMPORT_TIMEOUT_SECONDS, _public_status, _public_youtube_import_status, _youtube_import_timed_out


def test_public_status_defaults_old_jobs_to_stems_mode():
    status = _public_status(
        {
            "id": "old-job",
            "source_name": "song.mp3",
            "status": "completed",
            "expires_at": time.time() + 60,
        }
    )

    assert status["mode"] == "stems"
    assert "expires_at" not in status


def test_public_youtube_import_status_hides_internal_fields():
    status = _public_youtube_import_status(
        {
            "id": "import-job",
            "status": "queued",
            "progress": 5,
            "created_at": "2026-08-28T00:00:00Z",
            "created_unix": time.time(),
            "expires_at": time.time() + 60,
            "call_id": "internal-call",
        }
    )

    assert status["id"] == "import-job"
    assert "expires_at" not in status
    assert "call_id" not in status
    assert "created_unix" not in status


def test_youtube_import_timeout_uses_internal_creation_timestamp():
    assert _youtube_import_timed_out(
        {"created_unix": 100.0},
        now=100.0 + YOUTUBE_IMPORT_TIMEOUT_SECONDS,
    )
