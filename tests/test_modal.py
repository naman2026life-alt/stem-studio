import hashlib
import io
import json
import time
from pathlib import Path

import modal_app
from fastapi.testclient import TestClient
from pydub.generators import Sine
from modal_app import (
    HOME_WORKER_LEASE_SECONDS,
    YOUTUBE_IMPORT_TIMEOUT_SECONDS,
    _claim_home_worker_job,
    _public_status,
    _public_youtube_import_status,
    _worker_lease_is_valid,
    _write_youtube_import_status,
    _youtube_import_timed_out,
)


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
    now = time.time()
    status = _public_youtube_import_status(
        {
            "id": "import-job",
            "status": "processing_home",
            "progress": 35,
            "created_at": "2026-08-28T00:00:00Z",
            "created_unix": now,
            "expires_at": now + 60,
            "call_id": "internal-call",
            "canonical_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "cloud_outcome": "host_blocked",
            "helper_attempts": 1,
            "helper_last_seen": now,
            "lease_token_hash": "private-lease-hash",
            "lease_expires_at": now + 30,
            "worker_id": "private-worker",
            "result_sha256": "private-result-digest",
        }
    )

    assert status["id"] == "import-job"
    assert status["helper_online"] is True
    assert set(status) == {
        "id",
        "status",
        "progress",
        "created_at",
        "error",
        "title",
        "file_name",
        "file_url",
        "duration_seconds",
        "helper_online",
        "expires_in_seconds",
    }


def test_youtube_import_timeout_uses_internal_creation_timestamp():
    assert _youtube_import_timed_out(
        {"created_unix": 100.0},
        now=100.0 + YOUTUBE_IMPORT_TIMEOUT_SECONDS,
    )


def _create_helper_job(
    job_id: str,
    *,
    created_unix: float,
    expires_at: float,
    status: str = "waiting_for_helper",
    lease_expires_at: float | None = None,
) -> None:
    _write_youtube_import_status(
        job_id,
        id=job_id,
        status=status,
        progress=25,
        created_unix=created_unix,
        created_at="2026-08-28T00:00:00Z",
        expires_at=expires_at,
        canonical_url=f"https://www.youtube.com/watch?v={job_id:0<11}"[:43],
        lease_token_hash="expired-hash" if lease_expires_at is not None else None,
        lease_expires_at=lease_expires_at,
    )


def test_home_worker_claims_oldest_job_and_stores_only_hashed_lease(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(modal_app, "DATA_ROOT", tmp_path)
    now = 10_000.0
    _create_helper_job("older", created_unix=100.0, expires_at=now + 600)
    _create_helper_job("newer", created_unix=200.0, expires_at=now + 600)

    claim = _claim_home_worker_job("worker-" + "x" * 100, now=now)

    assert claim is not None
    assert claim["id"] == "older"
    assert claim["lease_seconds"] == HOME_WORKER_LEASE_SECONDS
    lease = str(claim["lease_token"])
    saved = json.loads((tmp_path / "youtube-imports" / "older" / "status.json").read_text())
    assert saved["status"] == "processing_home"
    assert saved["worker_id"] == ("worker-" + "x" * 100)[:80]
    assert saved["lease_token_hash"] != lease
    assert lease not in (tmp_path / "youtube-imports" / "older" / "status.json").read_text()
    assert _worker_lease_is_valid(saved, lease, now=now + HOME_WORKER_LEASE_SECONDS - 1)
    assert not _worker_lease_is_valid(saved, lease, now=now + HOME_WORKER_LEASE_SECONDS)
    assert not _worker_lease_is_valid(saved, "wrong-token", now=now + 1)

    next_claim = _claim_home_worker_job("second-worker", now=now + 1)
    assert next_claim is not None
    assert next_claim["id"] == "newer"


def test_home_worker_reclaims_job_after_lease_expires(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(modal_app, "DATA_ROOT", tmp_path)
    now = 20_000.0
    _create_helper_job(
        "abandoned",
        created_unix=100.0,
        expires_at=now + 600,
        status="processing_home",
        lease_expires_at=now - 1,
    )

    claim = _claim_home_worker_job("replacement-worker", now=now)

    assert claim is not None
    assert claim["id"] == "abandoned"
    saved = json.loads((tmp_path / "youtube-imports" / "abandoned" / "status.json").read_text())
    assert saved["worker_id"] == "replacement-worker"
    assert _worker_lease_is_valid(saved, str(claim["lease_token"]), now=now + 1)


class _NoopModalVolumeAction:
    def __call__(self) -> None:
        pass

    async def aio(self) -> None:
        pass


class _NoopModalVolume:
    def __init__(self) -> None:
        self.reload = _NoopModalVolumeAction()
        self.commit = _NoopModalVolumeAction()


def test_home_worker_broker_requires_auth_and_completes_claimed_mp3(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(modal_app, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(modal_app, "data_volume", _NoopModalVolume())
    monkeypatch.setenv("HOME_WORKER_SECRET", "private-worker-secret")
    now = time.time()
    _create_helper_job("broker-job", created_unix=now, expires_at=now + 600)
    broker = TestClient(modal_app.home_worker_api.local())

    assert broker.post("/worker/youtube/claim", data={"worker_id": "mac"}).status_code == 401
    claim_response = broker.post(
        "/worker/youtube/claim",
        data={"worker_id": "mac"},
        headers={"Authorization": "Bearer private-worker-secret"},
    )
    assert claim_response.status_code == 200
    claim = claim_response.json()

    audio = io.BytesIO()
    Sine(440).to_audio_segment(duration=100).export(audio, format="mp3", bitrate="192k")
    payload = audio.getvalue()
    bad_lease = broker.post(
        f"/worker/youtube/{claim['id']}/complete",
        data={"title": "My / song", "sha256": hashlib.sha256(payload).hexdigest()},
        files={"file": ("source.mp3", payload, "audio/mpeg")},
        headers={
            "Authorization": "Bearer private-worker-secret",
            "X-Worker-Lease": "wrong-lease",
        },
    )
    assert bad_lease.status_code == 409

    completed = broker.post(
        f"/worker/youtube/{claim['id']}/complete",
        data={"title": "My / song", "sha256": hashlib.sha256(payload).hexdigest()},
        files={"file": ("source.mp3", payload, "audio/mpeg")},
        headers={
            "Authorization": "Bearer private-worker-secret",
            "X-Worker-Lease": claim["lease_token"],
        },
    )

    assert completed.status_code == 200
    public = completed.json()
    assert public["status"] == "completed"
    assert public["file_name"] == "My song-youtube.mp3"
    assert "canonical_url" not in public
    assert "lease_token_hash" not in public
    assert (tmp_path / "youtube-imports" / "broker-job" / "source.mp3").read_bytes() == payload


def test_home_worker_broker_bounds_temporary_retries(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(modal_app, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(modal_app, "data_volume", _NoopModalVolume())
    monkeypatch.setenv("HOME_WORKER_SECRET", "private-worker-secret")
    now = time.time()
    _create_helper_job("retry-job", created_unix=now, expires_at=now + 600)
    broker = TestClient(modal_app.home_worker_api.local())
    auth = {"Authorization": "Bearer private-worker-secret"}

    for attempt in range(1, modal_app.HOME_WORKER_MAX_ATTEMPTS + 1):
        claim_response = broker.post("/worker/youtube/claim", data={"worker_id": "mac"}, headers=auth)
        assert claim_response.status_code == 200
        claim = claim_response.json()
        failed = broker.post(
            f"/worker/youtube/{claim['id']}/fail",
            data={"code": "temporary"},
            headers=auth | {"X-Worker-Lease": claim["lease_token"]},
        )
        assert failed.status_code == 200
        expected_status = "failed" if attempt == modal_app.HOME_WORKER_MAX_ATTEMPTS else "waiting_for_helper"
        assert failed.json()["status"] == expected_status

    saved = json.loads((tmp_path / "youtube-imports" / "retry-job" / "status.json").read_text())
    assert saved["helper_attempts"] == modal_app.HOME_WORKER_MAX_ATTEMPTS
    assert saved["canonical_url"] is None
    assert "lease_token" not in saved
