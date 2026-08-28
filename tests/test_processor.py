import io
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
import time
from pathlib import Path

from fastapi.testclient import TestClient
from pydub import AudioSegment
from pydub.generators import Sine

from processor import api
from stem_studio.youtube import ImportedYouTubeAudio


def test_processor_job_flow(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    api.jobs.clear()

    def fake_demucs(source: Path, output_root: Path, karaoke_only: bool = False):
        assert not karaoke_only
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
    assert job["mode"] == "stems"
    assert job["progress"] == 100
    for stem in ("vocals", "drums", "instrumental"):
        output = client.get(job[f"{stem}_url"])
        assert output.status_code == 200
        assert output.headers["content-type"] == "audio/wav"
        share_output = client.get(f"/jobs/{job_id}/share/{stem}")
        assert share_output.status_code == 200
        assert share_output.headers["content-type"] == "audio/mpeg"


def test_karaoke_job_only_returns_no_vocals_track(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    api.jobs.clear()

    def fake_demucs(source: Path, output_root: Path, karaoke_only: bool = False):
        assert karaoke_only
        output_root.mkdir(parents=True, exist_ok=True)
        Sine(440).to_audio_segment(duration=100).export(output_root / "vocals.wav", format="wav")
        instrumental = output_root / "no_vocals.wav"
        Sine(220).to_audio_segment(duration=100).export(instrumental, format="wav")
        return {"instrumental": instrumental}

    monkeypatch.setattr(api, "run_demucs", fake_demucs)
    client = TestClient(api.app)
    wav = io.BytesIO()
    Sine(330).to_audio_segment(duration=100).export(wav, format="wav")

    response = client.post(
        "/jobs",
        files={"file": ("test.wav", wav.getvalue(), "audio/wav")},
        data={"mode": "karaoke"},
    )
    assert response.status_code == 202
    job_id = response.json()["id"]
    for _ in range(50):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] == "completed":
            break
        time.sleep(0.02)

    assert job["status"] == "completed"
    assert job["mode"] == "karaoke"
    assert job["instrumental_url"]
    assert job["drums_url"] is None
    assert job["vocals_url"] is None
    output = client.get(job["instrumental_url"])
    assert output.status_code == 200
    assert "test-karaoke.wav" in output.headers["content-disposition"]
    share_output = client.get(f"/jobs/{job_id}/share/instrumental")
    assert share_output.status_code == 200
    assert "test-karaoke.mp3" in share_output.headers["content-disposition"]
    assert client.get(f"/jobs/{job_id}/files/drums").status_code == 404
    assert client.get(f"/jobs/{job_id}/files/vocals").status_code == 404
    assert not list((tmp_path / job_id).glob("separated-*"))


def test_rejects_unsupported_file(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    api.jobs.clear()
    client = TestClient(api.app)
    response = client.post("/jobs", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 415


def test_rejects_unknown_separation_mode(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    api.jobs.clear()
    client = TestClient(api.app)
    wav = io.BytesIO()
    Sine(330).to_audio_segment(duration=100).export(wav, format="wav")

    response = client.post(
        "/jobs",
        files={"file": ("test.wav", wav.getvalue(), "audio/wav")},
        data={"mode": "unknown"},
    )

    assert response.status_code == 422
    assert not api.jobs


def test_share_mp3_failure_keeps_wav_job_available(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    api.jobs.clear()

    def fake_demucs(source: Path, output_root: Path, karaoke_only: bool = False):
        assert not karaoke_only
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


def test_youtube_import_job_becomes_downloadable_mp3(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    api.youtube_imports.clear()

    def fake_import(url: str, directory: Path):
        assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / "source.mp3"
        Sine(440).to_audio_segment(duration=100).export(output, format="mp3")
        return ImportedYouTubeAudio(output, "My Song", "My Song-youtube.mp3", 0.1)

    monkeypatch.setattr(api, "import_youtube_audio", fake_import)
    client = TestClient(api.app)
    response = client.post(
        "/imports/youtube",
        data={"url": "https://youtu.be/dQw4w9WgXcQ", "rights_confirmed": "true"},
    )
    assert response.status_code == 202
    job_id = response.json()["id"]
    for _ in range(50):
        job = client.get(f"/imports/youtube/{job_id}").json()
        if job["status"] == "completed":
            break
        time.sleep(0.02)

    assert job["status"] == "completed"
    assert job["file_name"] == "My Song-youtube.mp3"
    imported = client.get(job["file_url"])
    assert imported.status_code == 200
    assert imported.headers["content-type"] == "audio/mpeg"
    assert "My%20Song-youtube.mp3" in imported.headers["content-disposition"]


def test_youtube_import_requires_rights_confirmation_and_safe_url(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    api.youtube_imports.clear()
    client = TestClient(api.app)

    no_rights = client.post(
        "/imports/youtube",
        data={"url": "https://youtu.be/dQw4w9WgXcQ", "rights_confirmed": "false"},
    )
    unsafe = client.post(
        "/imports/youtube",
        data={"url": "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ", "rights_confirmed": "true"},
    )

    assert no_rights.status_code == 422
    assert unsafe.status_code == 422
    assert not api.youtube_imports


def test_youtube_import_creation_requires_valid_signed_token(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", "test-shared-secret")
    api.youtube_imports.clear()
    client = TestClient(api.app)
    data = {"url": "https://youtu.be/dQw4w9WgXcQ", "rights_confirmed": "true"}

    assert client.post("/imports/youtube", data=data).status_code == 401

    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"test-shared-secret",
        f"{timestamp}:upload".encode(),
        hashlib.sha256,
    ).hexdigest()
    monkeypatch.setattr(
        api,
        "import_youtube_audio",
        lambda url, directory: (_ for _ in ()).throw(api.YouTubeImportError("test worker stop")),
    )
    response = client.post(
        "/imports/youtube",
        data=data,
        headers={"X-Stem-Timestamp": timestamp, "X-Stem-Signature": signature},
    )
    assert response.status_code == 202


def test_stalled_youtube_import_is_failed_and_no_longer_blocks_queue(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    api.youtube_imports.clear()
    now = time.time()
    api.youtube_imports["stalled"] = api.YouTubeImportJob(
        id="stalled",
        status="processing",
        progress=15,
        created_at=now - api.YOUTUBE_IMPORT_TIMEOUT_SECONDS - 1,
        expires_at=now + 60,
    )

    response = TestClient(api.app).get("/imports/youtube/stalled")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "processing window" in response.json()["error"]


def test_youtube_import_reservation_allows_only_one_active_job(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(api.youtube_executor, "submit", lambda *args, **kwargs: None)
    api.youtube_imports.clear()
    data = {"url": "https://youtu.be/dQw4w9WgXcQ", "rights_confirmed": "true"}

    def create_import() -> int:
        return TestClient(api.app).post("/imports/youtube", data=data).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: create_import(), range(2)))

    assert sorted(statuses) == [202, 429]
    assert len(api.youtube_imports) == 1


def test_youtube_import_releases_reservation_when_worker_cannot_start(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(
        api.youtube_executor,
        "submit",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("executor unavailable")),
    )
    api.youtube_imports.clear()

    response = TestClient(api.app).post(
        "/imports/youtube",
        data={"url": "https://youtu.be/dQw4w9WgXcQ", "rights_confirmed": "true"},
    )

    assert response.status_code == 503
    assert not api.youtube_imports
    assert not list((tmp_path / "youtube-imports").glob("*"))
