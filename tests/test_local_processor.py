"""Laptop service checks: private access, bounded work, and restart recovery."""

import asyncio
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from processor import api
from stem_studio.practice_jobs import write_record

COMPUTE_PATHS = [
    "/jobs", "/tools/extract-mp3", "/tools/trim-merge", "/tools/mix",
    "/imports/youtube", "/imports/youtube/" + "a" * 32 + "/cancel",
    "/practice/jobs", "/practice/compare",
]


def signed_headers(secret, *, age=0):
    stamp = str(int(time.time()) - age)
    signature = hmac.new(secret.encode(), f"{stamp}:upload".encode(), hashlib.sha256).hexdigest()
    return {"X-Stem-Timestamp": stamp, "X-Stem-Signature": signature}


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


@pytest.mark.parametrize("path", COMPUTE_PATHS)
@pytest.mark.parametrize("credential", ["missing", "forged", "expired", "future"])
def test_every_compute_endpoint_rejects_invalid_tokens_before_parsing_body(local_processor, monkeypatch, path, credential):
    secret = "direct-request-security-test" * 2
    monkeypatch.setenv("PROCESSOR_REQUIRE_AUTH", "1")
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", secret)
    if credential == "missing":
        headers = {}
    elif credential == "forged":
        headers = signed_headers("wrong-secret")
    else:
        headers = signed_headers(secret, age=301 if credential == "expired" else -301)
    # Deliberately malformed multipart would produce 400/422 if the body parser
    # ran first. Every compute path must return 401 before touching it.
    response = local_processor.post(path, content=b"not a multipart body",
                                    headers={**headers, "Content-Type": "multipart/form-data; boundary=missing"})
    assert response.status_code == 401
    assert not api.jobs and not api.youtube_imports and not api.mixes


def test_unauthenticated_asgi_request_never_reads_upload_bytes(local_processor, monkeypatch):
    monkeypatch.setenv("PROCESSOR_REQUIRE_AUTH", "1")
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", "direct-request-security-test" * 2)
    sent = []

    async def receive():
        raise AssertionError("An unauthorized request must never spool the upload body")

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "https", "path": "/jobs", "raw_path": b"/jobs", "query_string": b"",
             "headers": [(b"content-type", b"multipart/form-data; boundary=ignored")],
             "client": ("203.0.113.7", 50000), "server": ("studio.example", 443)}
    asyncio.run(api.app(scope, receive, send))
    assert next(message for message in sent if message["type"] == "http.response.start")["status"] == 401


def test_expired_token_response_is_readable_by_studio_browser(local_processor, monkeypatch):
    secret = "direct-request-security-test" * 2
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", secret)
    response = local_processor.post("/practice/compare", headers={
        **signed_headers(secret, age=301), "Origin": "http://localhost:3000",
    })
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["cache-control"] == "no-store"


def test_browser_preflight_needs_no_token_and_allows_signed_upload_headers(local_processor, monkeypatch):
    monkeypatch.setenv("PROCESSOR_REQUIRE_AUTH", "1")
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", "direct-request-security-test" * 2)
    response = local_processor.options("/practice/jobs", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-stem-timestamp,x-stem-signature",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-stem-signature" in response.headers["access-control-allow-headers"].lower()


def test_signed_json_cancellation_remains_supported(local_processor, monkeypatch):
    secret = "direct-request-security-test" * 2
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", secret)
    job_id = "a" * 32
    api.youtube_imports[job_id] = api.YouTubeImportJob(
        id=job_id, status="queued", progress=3, created_at=time.time(), expires_at=time.time() + 100,
    )
    response = local_processor.post(f"/imports/youtube/{job_id}/cancel", json={}, headers=signed_headers(secret))
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == "YouTube import cancelled."


def test_authenticated_oversized_body_is_rejected_before_form_parsing(local_processor, monkeypatch):
    secret = "direct-request-security-test" * 2
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", secret)
    response = local_processor.post("/jobs", content=b"", headers={
        **signed_headers(secret), "Content-Length": str(api._request_body_limit("/jobs") + 1),
    })
    assert response.status_code == 413


@pytest.mark.parametrize("declared_length", [None, "1"])
def test_streaming_body_limit_cannot_be_bypassed_with_chunking_or_false_length(local_processor, monkeypatch, declared_length):
    secret = "direct-request-security-test" * 2
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", secret)
    headers = {**signed_headers(secret), "Content-Type": "application/json"}
    if declared_length is not None:
        headers["Content-Length"] = declared_length

    def chunks():
        yield b'{"unnecessary": "'
        yield b"a" * (65 * 1024)
        yield b'"}'

    response = local_processor.post("/practice/compare", content=chunks(), headers=headers)
    assert response.status_code == 413, response.text


def test_valid_signed_request_reaches_compute_route(local_processor, monkeypatch):
    secret = "direct-request-security-test" * 2
    monkeypatch.setenv("PROCESSOR_REQUIRE_AUTH", "1")
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", secret)
    response = local_processor.post("/practice/compare", headers=signed_headers(secret),
                                    json={"take_job_id": "a" * 32, "reference_job_id": "b" * 32})
    assert response.status_code == 404  # Auth passes; these temporary result IDs do not exist.


def test_public_health_discloses_no_secret_or_storage_paths(local_processor, monkeypatch):
    secret = "direct-request-security-test" * 2
    monkeypatch.setenv("PROCESSOR_REQUIRE_AUTH", "1")
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", secret)
    for endpoint in ("/health", "/ready"):
        response = local_processor.get(endpoint)
        assert response.status_code in {200, 503}
        assert secret not in response.text
        assert str(api.WORK_ROOT) not in response.text
        assert str(api.MODEL_ROOT) not in response.text


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
