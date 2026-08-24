from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from stem_studio.audio import SUPPORTED_EXTENSIONS, run_demucs

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "150")) * 1024 * 1024
JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_SECONDS", "3600"))
MAX_PENDING_JOBS = int(os.environ.get("MAX_PENDING_JOBS", "3"))
UPLOAD_TOKEN_TTL_SECONDS = 300
CHUNK_BYTES = 1024 * 1024
WORK_ROOT = Path(os.environ.get("STEM_STUDIO_WORK_ROOT", tempfile.gettempdir())) / "stem-studio-api"
WORK_ROOT.mkdir(parents=True, exist_ok=True)


@dataclass
class Job:
    id: str
    source_name: str
    status: str
    progress: int
    created_at: float
    expires_at: float
    error: str | None = None
    vocals_url: str | None = None
    drums_url: str | None = None
    instrumental_url: str | None = None


jobs: dict[str, Job] = {}
jobs_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stem-studio")


def _allowed_origins() -> list[str]:
    configured = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    return [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]


app = FastAPI(title="Stem Studio Processor", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Stem-Timestamp", "X-Stem-Signature"],
)


def _public_job(job: Job) -> dict[str, object]:
    result = asdict(job)
    result.pop("expires_at")
    result["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(job.created_at))
    result["expires_in_seconds"] = max(0, round(job.expires_at - time.time()))
    return result


def _job_dir(job_id: str) -> Path:
    return WORK_ROOT / job_id


def _cleanup_expired() -> None:
    now = time.time()
    with jobs_lock:
        expired = [job_id for job_id, job in jobs.items() if job.expires_at <= now]
        for job_id in expired:
            jobs.pop(job_id, None)
    for job_id in expired:
        shutil.rmtree(_job_dir(job_id), ignore_errors=True)


def _verify_upload_token(timestamp: str | None, signature: str | None) -> None:
    secret = os.environ.get("PROCESSOR_SHARED_SECRET")
    if not secret:
        return
    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="A valid upload token is required.")
    try:
        token_time = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="The upload token is invalid.") from exc
    if abs(int(time.time()) - token_time) > UPLOAD_TOKEN_TTL_SECONDS:
        raise HTTPException(status_code=401, detail="The upload token expired. Please retry.")
    message = f"{timestamp}:upload".encode()
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="The upload token is invalid.")


def _set_job(job_id: str, **values: object) -> None:
    with jobs_lock:
        job = jobs.get(job_id)
        if job:
            for key, value in values.items():
                setattr(job, key, value)


def _process_job(job_id: str, source: Path) -> None:
    try:
        _set_job(job_id, status="processing", progress=8)
        outputs = run_demucs(source, _job_dir(job_id) / "separated")
        _set_job(job_id, progress=90)
        output_dir = _job_dir(job_id) / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        urls: dict[str, str] = {}
        for stem, source_path in outputs.items():
            destination = output_dir / f"{stem}.wav"
            shutil.move(str(source_path), destination)
            urls[f"{stem}_url"] = f"/jobs/{job_id}/files/{stem}"
        source.unlink(missing_ok=True)
        _set_job(job_id, status="completed", progress=100, **urls)
    except Exception as exc:
        source.unlink(missing_ok=True)
        _set_job(job_id, status="failed", error=str(exc)[:1000])


@app.get("/health")
def health() -> dict[str, object]:
    _cleanup_expired()
    with jobs_lock:
        active = sum(job.status in {"queued", "processing"} for job in jobs.values())
    return {"status": "ok", "active_jobs": active, "model": "htdemucs"}


@app.post("/jobs", status_code=202)
async def create_job(
    file: UploadFile,
    x_stem_timestamp: str | None = Header(default=None),
    x_stem_signature: str | None = Header(default=None),
) -> dict[str, object]:
    _cleanup_expired()
    _verify_upload_token(x_stem_timestamp, x_stem_signature)

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Use MP3, WAV, M4A, FLAC, AAC, or OGG.")
    with jobs_lock:
        pending = sum(job.status in {"queued", "processing"} for job in jobs.values())
    if pending >= MAX_PENDING_JOBS:
        raise HTTPException(status_code=429, detail="The processor is busy. Please try again shortly.")

    job_id = uuid.uuid4().hex
    directory = _job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=False)
    source = directory / f"source{suffix}"
    size = 0
    try:
        with source.open("wb") as destination:
            while chunk := await file.read(CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"Choose a file smaller than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
                destination.write(chunk)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await file.close()
    if size == 0:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    now = time.time()
    job = Job(
        id=job_id,
        source_name=Path(file.filename or "audio").name,
        status="queued",
        progress=3,
        created_at=now,
        expires_at=now + JOB_TTL_SECONDS,
    )
    with jobs_lock:
        jobs[job_id] = job
    executor.submit(_process_job, job_id, source)
    return _public_job(job)


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    _cleanup_expired()
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="This job expired or could not be found.")
        return _public_job(job)


@app.get("/jobs/{job_id}/files/{stem}")
def download_stem(job_id: str, stem: str) -> FileResponse:
    _cleanup_expired()
    if stem not in {"vocals", "drums", "instrumental"}:
        raise HTTPException(status_code=404, detail="Unknown stem.")
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job.status != "completed":
            raise HTTPException(status_code=404, detail="This output is not available.")
    path = _job_dir(job_id) / "outputs" / f"{stem}.wav"
    if not path.exists():
        raise HTTPException(status_code=404, detail="This output expired or could not be found.")
    safe_name = Path(job.source_name).stem or "song"
    return FileResponse(path, media_type="audio/wav", filename=f"{safe_name}-{stem}.wav")
