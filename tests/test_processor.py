import io
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from pydub import AudioSegment
from pydub.generators import Sine

from processor import api


def test_processor_job_flow(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    api.jobs.clear()

    def fake_demucs(source: Path, output_root: Path):
        output_root.mkdir(parents=True, exist_ok=True)
        outputs = {}
        for stem, frequency in {"vocals": 440, "drums": 110, "instrumental": 220}.items():
            path = output_root / f"{stem}.wav"
            Sine(frequency).to_audio_segment(duration=100).export(path, format="wav")
            outputs[stem] = path
        return outputs

    monkeypatch.setattr(api, "run_demucs", fake_demucs)
    client = TestClient(api.app)
    wav = io.BytesIO()
    Sine(330).to_audio_segment(duration=100).export(wav, format="wav")

    response = client.post("/jobs", files={"file": ("test.wav", wav.getvalue(), "audio/wav")})
    assert response.status_code == 202
    job_id = response.json()["id"]

    for _ in range(50):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] == "completed":
            break
        time.sleep(0.02)

    assert job["status"] == "completed"
    assert job["progress"] == 100
    for stem in ("vocals", "drums", "instrumental"):
        output = client.get(job[f"{stem}_url"])
        assert output.status_code == 200
        assert output.headers["content-type"] == "audio/wav"
        share_output = client.get(f"/jobs/{job_id}/share/{stem}")
        assert share_output.status_code == 200
        assert share_output.headers["content-type"] == "audio/mpeg"


def test_rejects_unsupported_file(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    api.jobs.clear()
    client = TestClient(api.app)
    response = client.post("/jobs", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 415


def test_share_mp3_failure_keeps_wav_job_available(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    api.jobs.clear()

    def fake_demucs(source: Path, output_root: Path):
        output_root.mkdir(parents=True, exist_ok=True)
        outputs = {}
        for stem in ("vocals", "drums", "instrumental"):
            path = output_root / f"{stem}.wav"
            Sine(220).to_audio_segment(duration=100).export(path, format="wav")
            outputs[stem] = path
        return outputs

    def fail_transcode(*args, **kwargs):
        raise RuntimeError("encode failed")

    monkeypatch.setattr(api, "run_demucs", fake_demucs)
    monkeypatch.setattr(api, "transcode_audio_to_mp3", fail_transcode)
    client = TestClient(api.app)
    wav = io.BytesIO()
    Sine(330).to_audio_segment(duration=100).export(wav, format="wav")

    response = client.post("/jobs", files={"file": ("test.wav", wav.getvalue(), "audio/wav")})
    job_id = response.json()["id"]
    for _ in range(50):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] == "completed":
            break
        time.sleep(0.02)

    assert job["status"] == "completed"
    assert client.get(job["instrumental_url"]).status_code == 200
    assert client.get(f"/jobs/{job_id}/share/instrumental").status_code == 404


def test_trim_merge_tool_clamps_end_to_duration(tmp_path: Path):
    client = TestClient(api.app)
    wav = io.BytesIO()
    Sine(330).to_audio_segment(duration=1000).export(wav, format="wav")

    response = client.post(
        "/tools/trim-merge",
        files={"file": ("test.wav", wav.getvalue(), "audio/wav")},
        data={
            "segments": json.dumps(
                [
                    {"start_seconds": 0, "end_seconds": 0.2},
                    {"start_seconds": 0.7, "end_seconds": 20},
                ]
            )
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert 480 <= len(AudioSegment.from_file(io.BytesIO(response.content))) <= 520


def test_trim_merge_rejects_empty_parts():
    client = TestClient(api.app)
    wav = io.BytesIO()
    Sine(330).to_audio_segment(duration=100).export(wav, format="wav")

    response = client.post(
        "/tools/trim-merge",
        files={"file": ("test.wav", wav.getvalue(), "audio/wav")},
        data={"segments": "[]"},
    )

    assert response.status_code == 422
