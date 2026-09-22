"""The same contract is exercised against local and hosted route adapters."""

import hashlib
import hmac
import io
import json
import subprocess
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydub.generators import Sine

import modal_app
from processor import api
from stem_studio import practice_jobs as practice


def audio_bytes(seconds=2):
    buffer = io.BytesIO()
    Sine(220).to_audio_segment(duration=seconds * 1000).export(buffer, format="wav")
    return buffer.getvalue()


def video_bytes(directory, suffix=".mp4", *, with_audio=True):
    output = directory / f"video-input{suffix}"
    command = [
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=32x32:r=10:d=2",
    ]
    if with_audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=220:duration=2"]
    command += ["-c:v", "mpeg4", "-q:v", "6"]
    if with_audio:
        command += ["-c:a", "aac", "-shortest"]
    subprocess.run([*command, str(output)], check=True, capture_output=True, timeout=30)
    return output.read_bytes()


def token_headers():
    stamp = str(int(time.time()))
    signature = hmac.new(b"practice-test", f"{stamp}:upload".encode(), hashlib.sha256).hexdigest()
    return {"X-Stem-Timestamp": stamp, "X-Stem-Signature": signature}


class NoopAction:
    def __call__(self):
        pass

    async def aio(self):
        pass


class NoopVolume:
    commit = NoopAction()
    reload = NoopAction()


@pytest.fixture(params=["portable", "modal"])
def harness(request, monkeypatch, tmp_path):
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", "practice-test")
    pending = []

    class QueueExecutor:
        def submit(self, function, *args, **kwargs):
            pending.append((function, args, kwargs))

    class QueueSpawn:
        async def aio(self, job_id, mode):
            assert mode == "practice"
            pending.append((practice.process_practice, (tmp_path / "practice", job_id), {}))

    if request.param == "portable":
        monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
        monkeypatch.setattr(api, "executor", QueueExecutor())
        application = api.app
    else:
        monkeypatch.setattr(modal_app, "DATA_ROOT", tmp_path)
        monkeypatch.setattr(modal_app, "data_volume", NoopVolume())
        monkeypatch.setattr(modal_app, "separate", types.SimpleNamespace(spawn=QueueSpawn()))
        application = modal_app.web.local()
    fake_pitch = types.ModuleType("stem_studio.pitch")

    def analyze(path, **kwargs):
        duration = practice.probe_duration_seconds(path)
        kwargs["progress"](0.5, "Following the melody")
        return {
            "duration_seconds": duration, "hop_seconds": 0.02, "engine": "mock",
            "frames": [{"time": 0, "hz": 220, "raw_hz": 220, "midi": 57, "confidence": 0.95}],
            "notes": [], "summary": {"voiced_seconds": duration}, "warnings": [],
        }

    fake_pitch.analyze_pitch = analyze
    monkeypatch.setitem(sys.modules, "stem_studio.pitch", fake_pitch)
    with TestClient(application) as client:
        yield types.SimpleNamespace(
            client=client, root=tmp_path / "practice", pending=pending,
            kind=request.param, base=tmp_path, engine=fake_pitch,
        )


def submit(harness, **controls):
    return harness.client.post(
        "/practice/jobs", files={"file": ("my-voice.wav", audio_bytes(), "audio/wav")},
        data=controls, headers=token_headers(),
    )


def run_next(harness):
    function, args, kwargs = harness.pending.pop(0)
    function(*args, **kwargs)


def test_practice_requires_auth_for_upload_and_comparison(harness):
    assert harness.client.post(
        "/practice/jobs", files={"file": ("v.wav", audio_bytes(), "audio/wav")},
    ).status_code == 401
    assert harness.client.post(
        "/practice/compare", json={"take_job_id": "a" * 32, "reference_job_id": "b" * 32},
    ).status_code == 401
    assert not harness.pending


def test_analysis_clips_before_worker_and_returns_playback(harness):
    response = submit(harness, start_seconds=0.5, duration_seconds=15)
    assert response.status_code == 202, response.text
    queued = response.json()
    job_id = queued["id"]
    assert queued["duration_seconds"] == 1.5  # requested end is capped
    assert queued["result"] is None
    assert "created_unix" not in queued and "fmin" not in queued
    assert practice.probe_duration_seconds(harness.root / job_id / "clip.wav") == pytest.approx(1.5, abs=0.01)
    assert not list((harness.root / job_id).glob("source.*"))
    run_next(harness)
    completed_response = harness.client.get(f"/practice/jobs/{job_id}")
    assert completed_response.headers["cache-control"] == "no-store"
    completed = completed_response.json()
    assert completed["status"] == "completed", completed
    assert completed["progress"] == 100
    assert completed["result"]["duration_seconds"] == pytest.approx(1.5, abs=0.01)
    assert not (harness.root / job_id / "clip.wav").exists()
    audio = harness.client.get(completed["audio_url"])
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/mpeg"
    assert audio.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("suffix", [".mp4", ".mov"])
def test_video_audio_is_clipped_before_analysis(harness, suffix):
    response = harness.client.post(
        "/practice/jobs",
        files={"file": (f"singing{suffix}", video_bytes(harness.base, suffix), "video/mp4")},
        data={"start_seconds": 0.5, "duration_seconds": 1}, headers=token_headers(),
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert practice.probe_duration_seconds(harness.root / job_id / "clip.wav") == pytest.approx(1, abs=0.02)
    assert not list((harness.root / job_id).glob(f"*{suffix}"))
    run_next(harness)
    result = harness.client.get(f"/practice/jobs/{job_id}").json()
    assert result["status"] == "completed"
    assert result["result"]["duration_seconds"] == pytest.approx(1, abs=0.02)


def test_video_without_audio_has_clear_error_and_never_queues(harness):
    response = harness.client.post(
        "/practice/jobs",
        files={"file": ("silent-video.mp4", video_bytes(harness.base, with_audio=False), "video/mp4")},
        headers=token_headers(),
    )
    assert response.status_code == 422
    assert "no audio track" in response.json()["detail"]
    assert not harness.pending
    assert not list(harness.root.glob("*/clip.wav"))


def test_mixed_audio_is_clipped_before_separation(harness, monkeypatch):
    seen = []

    def separate(source, output):
        seen.append(practice.probe_duration_seconds(source))
        return {"vocals": source}

    monkeypatch.setattr(practice, "run_demucs", separate)
    response = submit(harness, input_kind="mixed", start_seconds=1, duration_seconds=1)
    assert response.status_code == 202
    run_next(harness)
    job = harness.client.get(f"/practice/jobs/{response.json()['id']}").json()
    assert job["status"] == "completed"
    assert seen == [pytest.approx(1, abs=0.01)]
    assert any("harmonies" in warning for warning in job["result"]["warnings"])


@pytest.mark.parametrize("controls", [
    {"duration_seconds": 91}, {"duration_seconds": 0}, {"start_seconds": -1},
    {"start_seconds": 2}, {"fmin": 200, "fmax": 100}, {"fmin": "nan"},
    {"fmax": 2000}, {"input_kind": "anything"},
])
def test_invalid_controls_never_queue(harness, controls):
    response = submit(harness, **controls)
    assert response.status_code == 422, response.text
    assert not harness.pending
    assert not list(harness.root.glob("*/clip.wav"))


def test_invalid_and_overlong_media_do_not_queue(harness, monkeypatch):
    response = harness.client.post(
        "/practice/jobs", files={"file": ("bad.wav", b"broken", "audio/wav")}, headers=token_headers(),
    )
    assert response.status_code == 422
    monkeypatch.setattr(practice, "probe_duration_seconds", lambda path: 1801)
    assert submit(harness).status_code == 422
    assert not harness.pending


def test_upload_limit_is_enforced_before_analysis(harness, monkeypatch):
    monkeypatch.setattr(api if harness.kind == "portable" else modal_app, "MAX_UPLOAD_BYTES", 32)
    assert submit(harness).status_code == 413
    assert not harness.pending


def test_queue_cap_survives_concurrent_submissions(harness):
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: submit(harness), range(4)))
    assert sorted(response.status_code for response in responses) == [202, 202, 202, 429]
    assert len(harness.pending) == 3
    assert len(list(harness.root.glob("*/status.json"))) == 3


def test_failed_dispatch_releases_slot_and_deletes_input(harness, monkeypatch):
    if harness.kind == "portable":
        def fail(*args, **kwargs):
            raise RuntimeError("private executor failure")
        monkeypatch.setattr(api.executor, "submit", fail)
    else:
        async def fail(*args, **kwargs):
            raise RuntimeError("private spawn failure")
        monkeypatch.setattr(modal_app.separate.spawn, "aio", fail)
    response = submit(harness)
    assert response.status_code == 503
    assert "private" not in response.text
    record = json.loads(next(harness.root.glob("*/status.json")).read_text())
    assert record["status"] == "failed"
    assert not list(harness.root.glob("*/clip.wav"))


def test_worker_failure_is_safe_and_removes_audio(harness):
    def fail(*args, **kwargs):
        raise RuntimeError("private path and internal details")
    harness.engine.analyze_pitch = fail
    response = submit(harness)
    run_next(harness)
    job = harness.client.get(f"/practice/jobs/{response.json()['id']}").json()
    assert job["status"] == "failed"
    assert "private" not in job["error"]
    assert job["result"] is None and job["audio_url"] is None
    assert not list(harness.root.glob("*/clip.wav"))


def test_expired_unknown_and_not_ready_audio(harness):
    response = submit(harness)
    job_id = response.json()["id"]
    assert harness.client.get(f"/practice/jobs/{job_id}/audio").status_code == 404
    practice.write_record(harness.root, job_id, expires_at=time.time() - 1)
    assert harness.client.get(f"/practice/jobs/{job_id}").status_code == 410
    assert harness.client.get(f"/practice/jobs/{job_id}/audio").status_code == 410
    assert harness.client.get("/practice/jobs/not-a-valid-id").status_code == 404
    assert practice.cleanup_practice(harness.root)
    assert not (harness.root / job_id).exists()


def test_stalled_workers_stop_occupying_queue(harness):
    response = submit(harness)
    job_id = response.json()["id"]
    practice.write_record(harness.root, job_id, status="processing", started_at=time.time() - 700)
    record = harness.client.get(f"/practice/jobs/{job_id}").json()
    assert record["status"] == "failed"
    assert "too long" in record["error"]
    assert not (harness.root / job_id / "clip.wav").exists()


def test_progress_cannot_revive_a_timed_out_worker(harness):
    response = submit(harness)
    job_id = response.json()["id"]

    def analysis_after_timeout(path, **kwargs):
        practice.write_record(harness.root, job_id, status="failed", error="Analysis took too long.", progress=100)
        kwargs["progress"](0.6, "A late progress callback")
        raise AssertionError("The timeout should stop this worker")

    harness.engine.analyze_pitch = analysis_after_timeout
    run_next(harness)
    record = harness.client.get(f"/practice/jobs/{job_id}").json()
    assert record["status"] == "failed" and record["progress"] == 100
    assert record["error"] == "Analysis took too long."
    assert not list(harness.root.glob("*/clip.wav"))


def test_modal_shared_gpu_dispatch_preserves_practice_arguments(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(modal_app, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(modal_app, "data_volume", NoopVolume())
    monkeypatch.setattr(modal_app, "model_volume", NoopVolume())
    monkeypatch.setattr(practice, "process_practice", lambda *args, **kwargs: calls.append((args, kwargs)))
    modal_app.separate.local("a" * 32, "practice")
    assert calls[0][0] == (tmp_path / "practice", "a" * 32)
    assert calls[0][1]["device"] == "cuda"


def test_reuse_completed_vocal_result_does_not_reseparate(harness, monkeypatch):
    reference = "c" * 32
    outputs = (harness.base if harness.kind == "portable" else harness.base / "jobs") / reference / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "vocals.wav").write_bytes(audio_bytes())
    if harness.kind == "portable":
        monkeypatch.setitem(api.jobs, reference, api.Job(
            id=reference, source_name="song.wav", status="completed", progress=100,
            created_at=time.time(), expires_at=time.time() + 60, vocals_url="/vocals",
        ))
    else:
        (outputs.parent / "status.json").write_text(json.dumps({
            "id": reference, "source_name": "song.wav", "status": "completed",
            "expires_at": time.time() + 60, "vocals_url": "/vocals",
        }))
    response = harness.client.post(
        "/practice/jobs", data={"source_job_id": reference, "input_kind": "mixed"}, headers=token_headers(),
    )
    assert response.status_code == 202, response.text
    assert response.json()["input_kind"] == "isolated"
    assert response.json()["source_name"] == "song.wav"


def test_compare_only_accepts_completed_valid_results(harness, monkeypatch):
    from stem_studio import pitch_compare

    take_id, reference_id = "a" * 32, "b" * 32
    for job_id in (take_id, reference_id):
        practice.write_record(harness.root, job_id, id=job_id, status="completed", result={"frames": []},
                              expires_at=time.time() + 60)
    calls = []

    def compare(take, reference, **kwargs):
        calls.append(kwargs)
        return {"pitch_score": 88, "alignment": kwargs["alignment"]}

    monkeypatch.setattr(pitch_compare, "compare_pitch", compare)
    payload = {"take_job_id": take_id, "reference_job_id": reference_id, "alignment": "manual",
               "offset_seconds": 0.5, "transpose_semitones": -12}
    response = harness.client.post("/practice/compare", json=payload, headers=token_headers())
    assert response.status_code == 200
    assert calls == [{"alignment": "manual", "offset_seconds": 0.5, "transpose_semitones": -12}]
    payload["offset_seconds"] = 91
    assert harness.client.post("/practice/compare", json=payload, headers=token_headers()).status_code == 422
    payload["offset_seconds"] = 0
    practice.write_record(harness.root, take_id, status="processing")
    assert harness.client.post("/practice/compare", json=payload, headers=token_headers()).status_code == 409
