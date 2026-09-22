"""Laptop service checks: private access, bounded work, and restart recovery."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from processor import api
from stem_studio.practice_jobs import write_record


@pytest.fixture
def local_processor(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path / "data")
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "jobs", {})
    monkeypatch.setattr(api, "youtube_imports", {})
    monkeypatch.setattr(api, "mixes", {})
    monkeypatch.setattr(api, "media_busy", False)
    monkeypatch.delenv("PROCESSOR_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("PROCESSOR_SHARED_SECRET", raising=False)
    api.WORK_ROOT.mkdir()
    api.MODEL_ROOT.mkdir()
    return TestClient(api.app)


def test_remote_mode_refuses_startup_without_strong_shared_secret(local_processor, monkeypatch):
    monkeypatch.setenv("PROCESSOR_REQUIRE_AUTH", "1")
    with pytest.raises(RuntimeError, match="at least 32"):
        with local_processor:
            pass
    # The request path remains fail-closed even when lifespan is omitted by a
    # test runner or a misconfigured application server.
    response = local_processor.post("/imports/youtube", data={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert response.status_code == 503


def test_remote_mode_rejects_weak_secret_even_without_lifespan(local_processor, monkeypatch):
    monkeypatch.setenv("PROCESSOR_REQUIRE_AUTH", "1")
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", "short")
    response = local_processor.post("/imports/youtube", data={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert response.status_code == 503
    with pytest.raises(HTTPException) as error:
        api._verify_upload_token(None, None)
    assert error.value.status_code == 503


def test_remote_mode_rejects_wildcard_origins(local_processor, monkeypatch):
    monkeypatch.setenv("PROCESSOR_REQUIRE_AUTH", "1")
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", "x" * 40)
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="explicit ALLOWED_ORIGINS"):
        with local_processor:
            pass


def test_remote_requests_cannot_use_local_no_secret_bypass(local_processor):
    remote = TestClient(api.app, client=("203.0.113.1", 12345))
    response = remote.post("/imports/youtube", data={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert response.status_code == 503
    assert not api.youtube_imports


def test_readiness_checks_dependencies_without_loading_models(local_processor, monkeypatch):
    monkeypatch.setattr(api.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(api.shutil, "disk_usage", lambda path: SimpleNamespace(free=2 * 1024**3))
    monkeypatch.setattr(api.shutil, "which", lambda command: "/usr/bin/" + command)
    assert local_processor.get("/ready").status_code == 200
    monkeypatch.setattr(api.shutil, "which", lambda command: None if command == "ffmpeg" else "/bin/ffprobe")
    response = local_processor.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["ffmpeg"] is False
    assert response.headers["cache-control"] == "no-store"


def test_finished_mix_survives_restart_with_expiry(local_processor):
    job_id = "a" * 32
    job = api.MixJob(id=job_id, status="completed", created_at=time.time(), expires_at=time.time() + 100,
                     wav_url=f"/mixes/{job_id}/files/wav", mp3_url=f"/mixes/{job_id}/files/mp3")
    api._persist_job(job)
    (api._mix_dir(job_id) / "mix.mp3").write_bytes(b"generated test audio")
    with local_processor:
        response = local_processor.get(job.mp3_url)
        assert response.status_code == 200
        assert response.content == b"generated test audio"
        assert api.mixes[job_id].expires_at == job.expires_at


def test_restart_marks_interrupted_work_failed_and_removes_originals(local_processor):
    now = time.time()
    job_id, practice_id = "a" * 32, "b" * 32
    job = api.Job(id=job_id, source_name="recording.wav", status="processing", progress=20,
                  created_at=now, expires_at=now + 100)
    api._persist_job(job)
    source = api._job_dir(job_id) / "source.wav"
    source.write_bytes(b"temporary original")
    write_record(api.WORK_ROOT / "practice", practice_id, id=practice_id, status="queued", progress=3,
                 created_unix=now, expires_at=now + 100)
    clip = api.WORK_ROOT / "practice" / practice_id / "clip.wav"
    clip.write_bytes(b"temporary clip")
    with local_processor:
        restored = local_processor.get(f"/jobs/{job_id}").json()
        assert restored["status"] == "failed"
        assert "restarted" in restored["error"]
        practice = local_processor.get(f"/practice/jobs/{practice_id}").json()
        assert practice["status"] == "failed"
        assert not source.exists() and not clip.exists()
        assert local_processor.get("/health").json()["active_practice_jobs"] == 0


def test_restart_discards_expired_or_corrupt_status_without_retaining_audio(local_processor):
    for index, contents in (("a", "{broken"), ("b", json.dumps({"id": "b" * 32, "expires_at": float("nan")}))):
        directory = api.WORK_ROOT / (index * 32)
        directory.mkdir()
        (directory / "status.json").write_text(contents)
        (directory / "source.wav").write_bytes(b"private original")
    api._restore_jobs()
    assert not list(api.WORK_ROOT.glob("*/source.wav"))


def test_restart_never_follows_record_symlinks_outside_data_root(local_processor, tmp_path):
    outside = tmp_path / "unrelated-data"
    outside.mkdir()
    job_id = "a" * 32
    (outside / "source.wav").write_bytes(b"must remain untouched")
    job = api.Job(id=job_id, source_name="voice.wav", status="processing", progress=5,
                  created_at=time.time(), expires_at=time.time() + 100)
    (outside / "status.json").write_text(json.dumps(api.asdict(job)))
    (api.WORK_ROOT / job_id).symlink_to(outside, target_is_directory=True)
    api._restore_jobs()
    assert not api.jobs
    assert (outside / "source.wav").read_bytes() == b"must remain untouched"
    assert json.loads((outside / "status.json").read_text())["status"] == "processing"


def test_cancelled_youtube_job_cannot_be_revived_by_queued_worker(local_processor, monkeypatch):
    job_id = "c" * 32
    api.youtube_imports[job_id] = api.YouTubeImportJob(
        id=job_id, status="failed", progress=100, error="YouTube import cancelled.",
        created_at=time.time(), expires_at=time.time() + 100,
    )
    called = []
    monkeypatch.setattr(api, "import_youtube_audio", lambda *args: called.append(True))
    api._process_youtube_import(job_id, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert not called
    assert api.youtube_imports[job_id].status == "failed"
    assert api.youtube_imports[job_id].error == "YouTube import cancelled."


def test_media_work_cannot_overlap_model_processing(local_processor):
    now = time.time()
    api.jobs["a" * 32] = api.Job(id="a" * 32, source_name="voice.wav", status="processing", progress=50,
                                 created_at=now, expires_at=now + 100)
    called = []
    with pytest.raises(HTTPException) as error:
        asyncio.run(api._run_media(lambda: called.append(True)))
    assert error.value.status_code == 429
    assert not called
    assert api.media_busy is False


def test_media_reservation_is_released_after_failure(local_processor):
    def fail():
        raise ValueError("bad recording")
    with pytest.raises(ValueError, match="bad recording"):
        asyncio.run(api._run_media(fail))
    assert api.media_busy is False
    assert asyncio.run(api._run_media(lambda: "next edit")) == "next edit"
