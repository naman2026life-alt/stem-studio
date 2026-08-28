import hashlib
from pathlib import Path

import httpx
import pytest

from stem_studio import home_worker
from stem_studio.youtube import ImportedYouTubeAudio, YouTubeImportError


class RecordingHelperClient:
    def __init__(self) -> None:
        self.completed: list[tuple[str, str, bytes, str]] = []
        self.failed: list[tuple[str, str, str]] = []

    def heartbeat(self, job_id: str, lease_token: str) -> None:
        pass

    def complete(self, job_id: str, lease_token: str, audio: Path, title: str) -> None:
        self.completed.append((job_id, lease_token, audio.read_bytes(), title))

    def fail(self, job_id: str, lease_token: str, code: str) -> None:
        self.failed.append((job_id, lease_token, code))


def test_home_worker_processes_claim_and_returns_downloaded_mp3(monkeypatch):
    client = RecordingHelperClient()

    def fake_import(url: str, directory: Path) -> ImportedYouTubeAudio:
        assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        output = directory / "source.mp3"
        output.write_bytes(b"synthetic-mp3")
        return ImportedYouTubeAudio(output, "My recording", "My recording-youtube.mp3", 1.0)

    monkeypatch.setattr(home_worker, "import_youtube_audio", fake_import)

    home_worker.process_one_claim(
        client,
        {
            "id": "job-123456789",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "lease_token": "lease-token",
        },
    )

    assert client.completed == [("job-123456789", "lease-token", b"synthetic-mp3", "My recording")]
    assert client.failed == []


def test_home_worker_reports_safe_failure_code_to_broker(monkeypatch):
    client = RecordingHelperClient()

    def fail_import(url: str, directory: Path) -> ImportedYouTubeAudio:
        raise YouTubeImportError("private upstream detail was already redacted", code="restricted")

    monkeypatch.setattr(home_worker, "import_youtube_audio", fail_import)

    home_worker.process_one_claim(
        client,
        {
            "id": "job-123456789",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "lease_token": "lease-token",
        },
    )

    assert client.completed == []
    assert client.failed == [("job-123456789", "lease-token", "restricted")]


def test_helper_client_uploads_checksum_and_lease(tmp_path: Path):
    audio = tmp_path / "source.mp3"
    audio.write_bytes(b"synthetic-mp3-payload")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        requests.append(request)
        return httpx.Response(200, json={"status": "completed"})

    helper = home_worker.HelperClient("https://helper.example", "worker-secret")
    helper.client.close()
    helper.client = httpx.Client(
        base_url="https://helper.example",
        headers={"Authorization": "Bearer worker-secret"},
        transport=httpx.MockTransport(handler),
    )
    try:
        helper.complete("job-id", "lease-token", audio, "My title")
    finally:
        helper.close()

    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/worker/youtube/job-id/complete"
    assert request.headers["authorization"] == "Bearer worker-secret"
    assert request.headers["x-worker-lease"] == "lease-token"
    assert hashlib.sha256(audio.read_bytes()).hexdigest().encode() in request.content
    assert b"synthetic-mp3-payload" in request.content
    assert b"My title" in request.content


@pytest.mark.parametrize(
    "url",
    [
        "http://helper.example",
        "https://user:pass@helper.example",
        "https://helper.example:8443",
        "https://helper.example/path?secret=value",
    ],
)
def test_helper_rejects_unsafe_broker_urls(url: str):
    with pytest.raises(home_worker.HelperClientError):
        home_worker._validated_broker_url(url)


def test_helper_allows_https_broker_and_local_development_url():
    assert home_worker._validated_broker_url("https://helper.example/path/") == "https://helper.example/path"
    assert home_worker._validated_broker_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
