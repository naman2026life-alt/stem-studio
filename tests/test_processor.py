import io
import time
from pathlib import Path

from fastapi.testclient import TestClient
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


def test_rejects_unsupported_file(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    api.jobs.clear()
    client = TestClient(api.app)
    response = client.post("/jobs", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 415
