import time

from modal_app import _public_status


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
