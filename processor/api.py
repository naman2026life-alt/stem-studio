from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
import sys
from typing import Literal

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from stem_studio.audio import (
    MAX_SEPARATION_DURATION_SECONDS,
    SUPPORTED_EXTENSIONS,
    SUPPORTED_VIDEO_EXTENSIONS,
    extract_audio_to_mp3,
    mix_tracks,
    probe_duration_seconds,
    run_demucs,
    transcode_audio_to_mp3,
    trim_and_merge_audio,
)
from stem_studio.youtube import (
    YouTubeHostedBlockError,
    YouTubeImportError,
    YouTubeUrlError,
    canonicalize_youtube_url,
    import_youtube_audio,
)
from stem_studio.practice_jobs import process_practice, register_practice_routes

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "150")) * 1024 * 1024
JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_SECONDS", "3600"))
MAX_PENDING_JOBS = int(os.environ.get("MAX_PENDING_JOBS", "3"))
MAX_PENDING_IMPORTS = int(os.environ.get("MAX_PENDING_IMPORTS", "1"))
YOUTUBE_IMPORT_TIMEOUT_SECONDS = int(os.environ.get("YOUTUBE_IMPORT_TIMEOUT_SECONDS", "900"))
UPLOAD_TOKEN_TTL_SECONDS = 300
CHUNK_BYTES = 1024 * 1024
MAX_MIX_OFFSET_MS = 10 * 60 * 1000
MIN_MIX_GAIN_DB = -60.0
MAX_MIX_GAIN_DB = 24.0
SEPARATION_ERROR_MESSAGE = "The audio could not be separated. Check the file and try again."
MIX_ERROR_MESSAGE = "These tracks could not be mixed. Check both audio files and the timing controls."
RESOURCE_ID_PATTERN = re.compile(r"[a-f0-9]{32}")


def _default_data_root() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Stem Studio" / "processor"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "stem-studio" / "processor"


# STEM_STUDIO_DATA_DIR is an exact path. Preserve the older parent-directory
# setting so existing Docker and local deployments do not lose their outputs.
WORK_ROOT = Path(os.environ["STEM_STUDIO_DATA_DIR"]).expanduser() if os.environ.get("STEM_STUDIO_DATA_DIR") else (
    Path(os.environ["STEM_STUDIO_WORK_ROOT"]).expanduser() / "stem-studio-api"
    if os.environ.get("STEM_STUDIO_WORK_ROOT") else _default_data_root()
)
MODEL_ROOT = Path(os.environ.get(
    "STEM_STUDIO_MODEL_DIR", os.environ.get("TORCH_HOME", str(Path(__file__).resolve().parent.parent / ".model-cache")),
)).expanduser()
CPU_THREADS = max(1, min(8, int(os.environ.get("STEM_STUDIO_CPU_THREADS", "4"))))
os.environ["TORCH_HOME"] = str(MODEL_ROOT)
for _thread_setting in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_thread_setting] = str(CPU_THREADS)
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
    mode: Literal["stems", "karaoke"] = "stems"


@dataclass
class YouTubeImportJob:
    id: str
    status: str
    progress: int
    created_at: float
    expires_at: float
    error: str | None = None
    title: str | None = None
    file_name: str | None = None
    file_url: str | None = None
    duration_seconds: float | None = None


@dataclass
class MixJob:
    id: str
    status: str
    created_at: float
    expires_at: float
    wav_url: str
    mp3_url: str


jobs: dict[str, Job] = {}
youtube_imports: dict[str, YouTubeImportJob] = {}
mixes: dict[str, MixJob] = {}
jobs_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stem-studio")
youtube_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stem-studio-youtube")
media_busy = False


def _allowed_origins() -> list[str]:
    configured = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    return [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]


def _auth_required() -> bool:
    return os.environ.get("PROCESSOR_REQUIRE_AUTH", "").lower() in {"1", "true", "yes"}


def _validate_configuration() -> None:
    if _auth_required():
        secret = os.environ.get("PROCESSOR_SHARED_SECRET", "")
        if len(secret) < 32:
            raise RuntimeError("Remote mode requires a PROCESSOR_SHARED_SECRET of at least 32 characters.")
        if "*" in _allowed_origins():
            raise RuntimeError("Remote mode requires explicit ALLOWED_ORIGINS; a wildcard is not allowed.")
    for directory in (WORK_ROOT, MODEL_ROOT):
        directory.mkdir(parents=True, exist_ok=True)
        # Creating an actual file catches ACL/filesystem restrictions that a
        # simple os.access check can miss on macOS.
        with tempfile.TemporaryFile(dir=directory):
            pass


@asynccontextmanager
async def _lifespan(application: FastAPI):
    _validate_configuration()
    _restore_jobs()
    _cleanup_expired()

    async def cleanup_loop() -> None:
        while True:
            await asyncio.sleep(60)
            await asyncio.to_thread(_cleanup_expired)

    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Stem Studio Processor", version="1.1.0", lifespan=_lifespan)


def _request_body_limit(path: str) -> int:
    if path == "/practice/compare" or path.startswith("/imports/youtube"):
        return 64 * 1024
    # Two independent files are accepted only for mixing. Keep room for
    # multipart headers; _save_upload also enforces each file's exact cap.
    return MAX_UPLOAD_BYTES * (2 if path == "/tools/mix" else 1) + 1024 * 1024


class BoundedRequestBody:
    """Enforce limits while streaming, before multipart files can fill disk."""

    def __init__(self, application):
        self.app = application

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        limit = _request_body_limit(scope["path"])
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(status_code=413, detail="This upload is too large. Choose a smaller recording.")
            return message

        await self.app(scope, bounded_receive, send)


app.add_middleware(BoundedRequestBody)


@app.middleware("http")
async def protect_remote_requests(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        secret = os.environ.get("PROCESSOR_SHARED_SECRET", "")
        missing_remote_auth = _auth_required() and len(secret) < 32
        client_host = request.client.host if request.client else ""
        local_client = client_host in {"127.0.0.1", "::1", "localhost", "testclient"}
        if missing_remote_auth or (not secret and not local_client):
            return JSONResponse(status_code=503, content={"detail": "Remote processing authentication is not configured."})
        # Authenticate before FastAPI parses JSON/multipart bodies. Handler
        # checks remain as defence in depth, but cannot protect upload spooling.
        try:
            _verify_upload_token(request.headers.get("x-stem-timestamp"), request.headers.get("x-stem-signature"))
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                                headers={"Cache-Control": "no-store"})
        length = request.headers.get("content-length")
        if length is not None:
            try:
                size = int(length)
                if size < 0:
                    raise ValueError("Negative length")
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "The upload length is invalid."})
            if size > _request_body_limit(request.url.path):
                return JSONResponse(status_code=413, content={"detail": "This upload is too large. Choose a smaller recording."})
    response = await call_next(request)
    # Expiring recordings and session metadata must not remain in tunnel/CDN
    # caches after the processor has removed them.
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


# CORS wraps authentication too, so the real studio can read an expired-token
# response instead of seeing an opaque browser network error.
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


def _public_youtube_import(job: YouTubeImportJob) -> dict[str, object]:
    result = asdict(job)
    result.pop("expires_at")
    result["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(job.created_at))
    result["expires_in_seconds"] = max(0, round(job.expires_at - time.time()))
    return result


def _public_mix(job: MixJob) -> dict[str, object]:
    result = asdict(job)
    result.pop("expires_at")
    result["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(job.created_at))
    result["expires_in_seconds"] = max(0, round(job.expires_at - time.time()))
    return result


def _job_dir(job_id: str) -> Path:
    return WORK_ROOT / job_id


def _youtube_import_dir(job_id: str) -> Path:
    return WORK_ROOT / "youtube-imports" / job_id


def _mix_dir(mix_id: str) -> Path:
    return WORK_ROOT / "mixes" / mix_id


def _persist_job(job: Job | YouTubeImportJob | MixJob) -> None:
    _validate_resource_id(job.id, "job")
    if isinstance(job, Job):
        directory = _job_dir(job.id)
    elif isinstance(job, YouTubeImportJob):
        directory = _youtube_import_dir(job.id)
    else:
        directory = _mix_dir(job.id)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"status-{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(asdict(job), allow_nan=False))
    temporary.replace(directory / "status.json")


def _restore_jobs() -> None:
    """Keep finished results across restarts; never silently rerun old work."""
    from stem_studio.practice_jobs import write_record

    now = time.time()
    with jobs_lock:
        for directory, records, job_type in (
            (WORK_ROOT, jobs, Job), (WORK_ROOT / "youtube-imports", youtube_imports, YouTubeImportJob),
            (WORK_ROOT / "mixes", mixes, MixJob),
        ):
            records.clear()
            for path in directory.glob("*/status.json"):
                if not RESOURCE_ID_PATTERN.fullmatch(path.parent.name) or path.parent.is_symlink() or path.is_symlink():
                    continue
                try:
                    data = json.loads(path.read_text())
                    job = job_type(**data)
                    if (job.id != path.parent.name or not math.isfinite(job.expires_at)
                            or not math.isfinite(job.created_at)
                            or job.status not in {"queued", "processing", "completed", "failed"}):
                        shutil.rmtree(path.parent, ignore_errors=True)
                        continue
                    if job.expires_at <= now:
                        shutil.rmtree(path.parent, ignore_errors=True)
                        continue
                    if job.status in {"queued", "processing"}:
                        job.status = "failed"
                        job.progress = 100
                        job.error = "The processor restarted during this job. Please submit the recording again."
                        for source in path.parent.glob("source.*"):
                            source.unlink(missing_ok=True)
                        _persist_job(job)
                    records[job.id] = job
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    shutil.rmtree(path.parent, ignore_errors=True)
                    continue
        for path in (WORK_ROOT / "practice").glob("*/status.json"):
            if not RESOURCE_ID_PATTERN.fullmatch(path.parent.name) or path.parent.is_symlink() or path.is_symlink():
                continue
            try:
                data = json.loads(path.read_text())
                if data.get("status") in {"queued", "processing"}:
                    write_record(WORK_ROOT / "practice", path.parent.name, status="failed", progress=100,
                                 stage="Processor restarted", error="The processor restarted. Analyze this recording again.")
                    (path.parent / "clip.wav").unlink(missing_ok=True)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue


def _active_practice_count() -> int:
    active = 0
    for path in (WORK_ROOT / "practice").glob("*/status.json"):
        try:
            record = json.loads(path.read_text())
            active += record.get("status") in {"queued", "processing"} and float(record["expires_at"]) > time.time()
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return active


async def _run_media(function, *args, **kwargs):
    """A single model/media worker bounds CPU and peak memory on a laptop."""
    global media_busy
    with jobs_lock:
        if media_busy or any(job.status in {"queued", "processing"} for job in jobs.values()) or _active_practice_count():
            raise HTTPException(status_code=429, detail="The Mac is processing another recording. Try this action when it finishes.")
        media_busy = True
    try:
        return await asyncio.get_running_loop().run_in_executor(executor, partial(function, *args, **kwargs))
    finally:
        with jobs_lock:
            media_busy = False


def _cleanup_expired() -> None:
    from stem_studio.practice_jobs import cleanup_practice, expire_stalled

    expire_stalled(WORK_ROOT / "practice")
    cleanup_practice(WORK_ROOT / "practice")
    now = time.time()
    with jobs_lock:
        expired = [job_id for job_id, job in jobs.items() if job.expires_at <= now]
        for job_id in expired:
            jobs.pop(job_id, None)
        for job in youtube_imports.values():
            if job.status in {"queued", "processing"} and now - job.created_at >= YOUTUBE_IMPORT_TIMEOUT_SECONDS:
                job.status = "failed"
                job.progress = 100
                job.error = "This YouTube import exceeded its processing window. Please try again."
                _persist_job(job)
        expired_imports = [job_id for job_id, job in youtube_imports.items() if job.expires_at <= now]
        for job_id in expired_imports:
            youtube_imports.pop(job_id, None)
        expired_mixes = [mix_id for mix_id, mix in mixes.items() if mix.expires_at <= now]
        for mix_id in expired_mixes:
            mixes.pop(mix_id, None)
    for job_id in expired:
        shutil.rmtree(_job_dir(job_id), ignore_errors=True)
    for job_id in expired_imports:
        shutil.rmtree(_youtube_import_dir(job_id), ignore_errors=True)
    for mix_id in expired_mixes:
        shutil.rmtree(_mix_dir(mix_id), ignore_errors=True)


def _verify_upload_token(timestamp: str | None, signature: str | None) -> None:
    secret = os.environ.get("PROCESSOR_SHARED_SECRET")
    if _auth_required() and len(secret or "") < 32:
        raise HTTPException(status_code=503, detail="Remote processing authentication is not configured.")
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


async def _save_upload(file: UploadFile, directory: Path, allowed_extensions: set[str]) -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed_extensions:
        raise HTTPException(status_code=415, detail="This file format is not supported for that action.")
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / f"source{suffix}"
    size = 0
    try:
        with source.open("wb") as destination:
            while chunk := await file.read(CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Choose a file smaller than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                    )
                destination.write(chunk)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await file.close()
    if size == 0:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return source


async def _validate_separation_source(source: Path, directory: Path) -> None:
    try:
        duration = await asyncio.to_thread(probe_duration_seconds, source)
    except (OSError, RuntimeError, ValueError) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(status_code=422, detail="This audio file could not be read. Try converting it to MP3 or WAV.") from exc
    if duration > MAX_SEPARATION_DURATION_SECONDS:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(status_code=422, detail="Choose an audio file that is 30 minutes or shorter for separation.")


def _parse_segments(value: str) -> list[tuple[float, float | None]]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="The trim parts are invalid.") from exc
    if not isinstance(payload, list) or not payload:
        raise HTTPException(status_code=422, detail="Add at least one part to trim and merge.")
    if len(payload) > 50:
        raise HTTPException(status_code=422, detail="Use no more than 50 trim parts at a time.")
    result: list[tuple[float, float | None]] = []
    for index, part in enumerate(payload, start=1):
        if not isinstance(part, dict):
            raise HTTPException(status_code=422, detail=f"Part {index} is invalid.")
        start = part.get("start_seconds")
        end = part.get("end_seconds")
        if isinstance(start, bool) or not isinstance(start, (int, float)):
            raise HTTPException(status_code=422, detail=f"Part {index} has an invalid start time.")
        if end is not None and (isinstance(end, bool) or not isinstance(end, (int, float))):
            raise HTTPException(status_code=422, detail=f"Part {index} has an invalid end time.")
        result.append((float(start), None if end is None else float(end)))
    return result


def _validate_resource_id(value: str, resource_name: str) -> str:
    if not RESOURCE_ID_PATTERN.fullmatch(value):
        raise HTTPException(status_code=404, detail=f"This {resource_name} could not be found.")
    return value


def _validate_mix_controls(
    offset_ms: int,
    trim_start_seconds: float,
    trim_end_seconds: float,
    vocal_gain_db: float,
    instrumental_gain_db: float,
) -> None:
    values = (trim_start_seconds, trim_end_seconds, vocal_gain_db, instrumental_gain_db)
    if any(isinstance(value, bool) or not math.isfinite(float(value)) for value in values):
        raise HTTPException(status_code=422, detail="The mix controls contain an invalid number.")
    if abs(offset_ms) > MAX_MIX_OFFSET_MS:
        raise HTTPException(status_code=422, detail="Keep the vocal offset within 10 minutes.")
    if trim_start_seconds < 0 or trim_end_seconds < 0:
        raise HTTPException(status_code=422, detail="Vocal trim times cannot be negative.")
    if trim_end_seconds > 0 and trim_end_seconds <= trim_start_seconds:
        raise HTTPException(status_code=422, detail="Vocal trim end must be after trim start.")
    if not MIN_MIX_GAIN_DB <= vocal_gain_db <= MAX_MIX_GAIN_DB:
        raise HTTPException(status_code=422, detail="Keep vocal gain between -60 dB and +24 dB.")
    if not MIN_MIX_GAIN_DB <= instrumental_gain_db <= MAX_MIX_GAIN_DB:
        raise HTTPException(status_code=422, detail="Keep instrumental gain between -60 dB and +24 dB.")


def _completed_instrumental_path(job_id: str) -> Path:
    _validate_resource_id(job_id, "separation job")
    with jobs_lock:
        job = jobs.get(job_id)
        if (
            not job
            or job.status != "completed"
            or job.expires_at <= time.time()
            or not job.instrumental_url
        ):
            raise HTTPException(status_code=404, detail="This instrumental is not available.")
        path = _job_dir(job_id) / "outputs" / "instrumental.wav"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="This instrumental expired or could not be found.")
    return path


def _set_job(job_id: str, **values: object) -> None:
    with jobs_lock:
        job = jobs.get(job_id)
        if job:
            for key, value in values.items():
                setattr(job, key, value)
            _persist_job(job)


def _set_youtube_import(job_id: str, **values: object) -> bool:
    with jobs_lock:
        job = youtube_imports.get(job_id)
        if job and job.status not in {"failed", "completed"}:
            for key, value in values.items():
                setattr(job, key, value)
            _persist_job(job)
            return True
    return False


def _process_job(job_id: str, source: Path, mode: Literal["stems", "karaoke"] = "stems") -> None:
    try:
        _set_job(job_id, status="processing", progress=8)
        with tempfile.TemporaryDirectory(prefix="separated-", dir=_job_dir(job_id)) as temporary:
            outputs = run_demucs(
                source,
                Path(temporary),
                karaoke_only=mode == "karaoke",
            )
            _set_job(job_id, progress=90)
            output_dir = _job_dir(job_id) / "outputs"
            output_dir.mkdir(parents=True, exist_ok=True)
            urls: dict[str, str] = {}
            for stem, source_path in outputs.items():
                destination = output_dir / f"{stem}.wav"
                shutil.move(str(source_path), destination)
                try:
                    transcode_audio_to_mp3(destination, output_dir / f"{stem}.mp3")
                except (OSError, RuntimeError, ValueError):
                    # The WAV is the canonical result; the UI can fall back to it if
                    # the smaller convenience copy cannot be encoded.
                    pass
                urls[f"{stem}_url"] = f"/jobs/{job_id}/files/{stem}"
        source.unlink(missing_ok=True)
        _set_job(job_id, status="completed", progress=100, **urls)
    except Exception:
        source.unlink(missing_ok=True)
        _set_job(job_id, status="failed", progress=100, error=SEPARATION_ERROR_MESSAGE)


def _process_youtube_import(job_id: str, canonical_url: str) -> None:
    directory = _youtube_import_dir(job_id)
    try:
        if not _set_youtube_import(job_id, status="processing", progress=15):
            return
        imported = import_youtube_audio(canonical_url, directory)
        with jobs_lock:
            job = youtube_imports.get(job_id)
            if not job or job.status not in {"queued", "processing"}:
                shutil.rmtree(directory, ignore_errors=True)
                if job:
                    _persist_job(job)
                return
            job.status = "completed"
            job.progress = 100
            job.title = imported.title
            job.file_name = imported.download_name
            job.file_url = f"/imports/youtube/{job_id}/file"
            job.duration_seconds = round(imported.duration_seconds, 3)
            _persist_job(job)
    except YouTubeHostedBlockError:
        shutil.rmtree(directory, ignore_errors=True)
        _set_youtube_import(
            job_id,
            status="failed",
            error="YouTube blocked this processor's network. Run the import from a permitted home connection or upload the audio file.",
            progress=100,
        )
    except YouTubeImportError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        _set_youtube_import(job_id, status="failed", error=str(exc), progress=100)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        _set_youtube_import(job_id, status="failed", error="YouTube audio import failed. Please try again.", progress=100)


@app.get("/health")
def health() -> dict[str, object]:
    _cleanup_expired()
    with jobs_lock:
        active = sum(job.status in {"queued", "processing"} for job in jobs.values())
        active_imports = sum(job.status in {"queued", "processing"} for job in youtube_imports.values())
    return {"status": "ok", "active_jobs": active, "active_youtube_imports": active_imports,
            "active_practice_jobs": _active_practice_count(), "active_media_jobs": int(media_busy),
            "model": "htdemucs", "compute": "local", "model_workers": 1, "cpu_threads": CPU_THREADS}


@app.get("/ready")
def readiness() -> JSONResponse:
    """Readiness checks do not download or load either neural model."""
    checks = {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "separation_package": importlib.util.find_spec("demucs") is not None,
        "pitch_package": importlib.util.find_spec("torchcrepe") is not None,
        "data_writable": WORK_ROOT.is_dir() and os.access(WORK_ROOT, os.W_OK),
        "models_writable": MODEL_ROOT.is_dir() and os.access(MODEL_ROOT, os.W_OK),
        "remote_auth": not _auth_required() or len(os.environ.get("PROCESSOR_SHARED_SECRET", "")) >= 32,
    }
    try:
        checks["disk_space"] = shutil.disk_usage(WORK_ROOT).free >= 1024 * 1024 * 1024
    except OSError:
        checks["disk_space"] = False
    ready = all(checks.values())
    return JSONResponse(status_code=200 if ready else 503,
                        content={"status": "ready" if ready else "not_ready", "checks": checks})


@app.post("/jobs", status_code=202)
async def create_job(
    file: UploadFile = File(...),
    mode: str = Form("stems"),
    x_stem_timestamp: str | None = Header(default=None),
    x_stem_signature: str | None = Header(default=None),
) -> dict[str, object]:
    _cleanup_expired()
    _verify_upload_token(x_stem_timestamp, x_stem_signature)

    source_name = Path(file.filename or "audio").name
    suffix = Path(source_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Use MP3, WAV, M4A, FLAC, AAC, OGG, or WEBM audio.")
    if mode not in {"stems", "karaoke"}:
        raise HTTPException(status_code=422, detail="Choose either stems or karaoke mode.")
    validated_mode: Literal["stems", "karaoke"] = "karaoke" if mode == "karaoke" else "stems"
    job_id = uuid.uuid4().hex
    directory = _job_dir(job_id)
    source = await _save_upload(file, directory, SUPPORTED_EXTENSIONS)
    await _validate_separation_source(source, directory)

    now = time.time()
    job = Job(
        id=job_id,
        source_name=source_name,
        mode=validated_mode,
        status="queued",
        progress=3,
        created_at=now,
        expires_at=now + JOB_TTL_SECONDS,
    )
    with jobs_lock:
        pending = sum(existing.status in {"queued", "processing"} for existing in jobs.values())
        if pending >= MAX_PENDING_JOBS:
            shutil.rmtree(directory, ignore_errors=True)
            raise HTTPException(status_code=429, detail="The processor is busy. Please try again shortly.")
        jobs[job_id] = job
        _persist_job(job)
    try:
        executor.submit(_process_job, job_id, source, validated_mode)
    except RuntimeError as exc:
        source.unlink(missing_ok=True)
        _set_job(
            job_id,
            status="failed",
            progress=100,
            error="The separation worker could not start. Please try again shortly.",
        )
        raise HTTPException(
            status_code=503,
            detail="The separation worker could not start. Please try again shortly.",
        ) from exc
    return _public_job(job)


@app.post("/tools/extract-mp3")
async def extract_mp3(
    file: UploadFile = File(...),
    x_stem_timestamp: str | None = Header(default=None),
    x_stem_signature: str | None = Header(default=None),
) -> FileResponse:
    _verify_upload_token(x_stem_timestamp, x_stem_signature)
    source_name = Path(file.filename or "video").name
    directory = Path(tempfile.mkdtemp(prefix="stem-studio-extract-"))
    source = await _save_upload(file, directory, SUPPORTED_VIDEO_EXTENSIONS)
    output = directory / f"{Path(source_name).stem or 'video'}.mp3"
    try:
        await _run_media(extract_audio_to_mp3, source, output)
    except HTTPException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(
        output,
        media_type="audio/mpeg",
        filename=output.name,
        background=BackgroundTask(shutil.rmtree, directory, ignore_errors=True),
    )


@app.post("/tools/trim-merge")
async def trim_merge(
    file: UploadFile = File(...),
    segments: str = Form(...),
    x_stem_timestamp: str | None = Header(default=None),
    x_stem_signature: str | None = Header(default=None),
) -> FileResponse:
    _verify_upload_token(x_stem_timestamp, x_stem_signature)
    parsed_segments = _parse_segments(segments)
    source_name = Path(file.filename or "audio").name
    directory = Path(tempfile.mkdtemp(prefix="stem-studio-trim-"))
    source = await _save_upload(file, directory, SUPPORTED_EXTENSIONS)
    output = directory / f"{Path(source_name).stem or 'audio'}-trimmed.mp3"
    try:
        await _run_media(trim_and_merge_audio, source, parsed_segments, output)
    except HTTPException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(
        output,
        media_type="audio/mpeg",
        filename=output.name,
        background=BackgroundTask(shutil.rmtree, directory, ignore_errors=True),
    )


@app.post("/tools/mix")
async def create_mix(
    vocal: UploadFile = File(...),
    instrumental: UploadFile | None = File(default=None),
    instrumental_job_id: str | None = Form(default=None),
    offset_ms: int = Form(0),
    trim_start_seconds: float = Form(0),
    trim_end_seconds: float = Form(0),
    vocal_gain_db: float = Form(0),
    instrumental_gain_db: float = Form(0),
    x_stem_timestamp: str | None = Header(default=None),
    x_stem_signature: str | None = Header(default=None),
) -> dict[str, object]:
    _cleanup_expired()
    _verify_upload_token(x_stem_timestamp, x_stem_signature)
    reference_id = (instrumental_job_id or "").strip() or None
    if (instrumental is None) == (reference_id is None):
        if instrumental is not None:
            await instrumental.close()
        await vocal.close()
        raise HTTPException(
            status_code=422,
            detail="Provide either an instrumental file or a completed instrumental job, but not both.",
        )
    try:
        _validate_mix_controls(
            offset_ms,
            trim_start_seconds,
            trim_end_seconds,
            vocal_gain_db,
            instrumental_gain_db,
        )
    except HTTPException:
        if instrumental is not None:
            await instrumental.close()
        await vocal.close()
        raise

    if reference_id is not None:
        try:
            instrumental_source = _completed_instrumental_path(reference_id)
        except HTTPException:
            await vocal.close()
            raise
    else:
        instrumental_source = Path()

    mix_id = uuid.uuid4().hex
    mix_directory = _mix_dir(mix_id)
    try:
        if instrumental is not None:
            instrumental_source = await _save_upload(
                instrumental,
                mix_directory / "instrumental-input",
                SUPPORTED_EXTENSIONS,
            )
        vocal_source = await _save_upload(
            vocal,
            mix_directory / "vocal-input",
            SUPPORTED_EXTENSIONS,
        )
        wav_path, mp3_path = await _run_media(
            mix_tracks,
            instrumental_source,
            vocal_source,
            mix_directory,
            offset_ms=offset_ms,
            trim_start_seconds=trim_start_seconds,
            trim_end_seconds=trim_end_seconds,
            vocal_gain_db=vocal_gain_db,
            instrumental_gain_db=instrumental_gain_db,
        )
        wav_path.replace(mix_directory / "mix.wav")
        mp3_path.replace(mix_directory / "mix.mp3")
        shutil.rmtree(mix_directory / "instrumental-input", ignore_errors=True)
        shutil.rmtree(mix_directory / "vocal-input", ignore_errors=True)
        now = time.time()
        job = MixJob(
            id=mix_id,
            status="completed",
            created_at=now,
            expires_at=now + JOB_TTL_SECONDS,
            wav_url=f"/mixes/{mix_id}/files/wav",
            mp3_url=f"/mixes/{mix_id}/files/mp3",
        )
        with jobs_lock:
            mixes[mix_id] = job
            _persist_job(job)
    except HTTPException:
        shutil.rmtree(mix_directory, ignore_errors=True)
        raise
    except ValueError as exc:
        shutil.rmtree(mix_directory, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        shutil.rmtree(mix_directory, ignore_errors=True)
        raise HTTPException(status_code=422, detail=MIX_ERROR_MESSAGE) from exc
    return _public_mix(job)


@app.post("/imports/youtube", status_code=202)
async def create_youtube_import(
    url: str = Form(...),
    rights_confirmed: bool = Form(False),
    x_stem_timestamp: str | None = Header(default=None),
    x_stem_signature: str | None = Header(default=None),
) -> dict[str, object]:
    _cleanup_expired()
    _verify_upload_token(x_stem_timestamp, x_stem_signature)
    if not rights_confirmed:
        raise HTTPException(status_code=422, detail="Confirm that you own this audio or have permission to download it.")
    try:
        canonical_url = canonicalize_youtube_url(url)
    except YouTubeUrlError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job_id = uuid.uuid4().hex
    directory = _youtube_import_dir(job_id)
    now = time.time()
    job = YouTubeImportJob(
        id=job_id,
        status="queued",
        progress=5,
        created_at=now,
        expires_at=now + JOB_TTL_SECONDS,
    )
    with jobs_lock:
        pending = sum(job.status in {"queued", "processing"} for job in youtube_imports.values())
        if pending >= MAX_PENDING_IMPORTS:
            raise HTTPException(
                status_code=429,
                detail="A YouTube import is already running. Please try again when it finishes.",
            )
        directory.mkdir(parents=True, exist_ok=True)
        youtube_imports[job_id] = job
        _persist_job(job)
    try:
        youtube_executor.submit(_process_youtube_import, job_id, canonical_url)
    except RuntimeError as exc:
        with jobs_lock:
            youtube_imports.pop(job_id, None)
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(
            status_code=503,
            detail="The YouTube importer could not start. Please try again shortly.",
        ) from exc
    return _public_youtube_import(job)


@app.get("/imports/youtube/{job_id}")
def get_youtube_import(job_id: str) -> dict[str, object]:
    _cleanup_expired()
    with jobs_lock:
        job = youtube_imports.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="This YouTube import expired or could not be found.")
        return _public_youtube_import(job)


@app.post("/imports/youtube/{job_id}/cancel")
def cancel_youtube_import(
    job_id: str,
    x_stem_timestamp: str | None = Header(default=None),
    x_stem_signature: str | None = Header(default=None),
) -> dict[str, object]:
    _verify_upload_token(x_stem_timestamp, x_stem_signature)
    with jobs_lock:
        job = youtube_imports.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="This YouTube import expired or could not be found.")
        if job.status in {"queued", "processing"}:
            job.status = "failed"
            job.progress = 100
            job.error = "YouTube import cancelled."
            _persist_job(job)
        return _public_youtube_import(job)


@app.get("/imports/youtube/{job_id}/file")
def download_youtube_import(job_id: str) -> FileResponse:
    _cleanup_expired()
    with jobs_lock:
        job = youtube_imports.get(job_id)
        if not job or job.status != "completed" or not job.file_name:
            raise HTTPException(status_code=404, detail="This imported audio is not available.")
        file_name = job.file_name
    path = _youtube_import_dir(job_id) / "source.mp3"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="This imported audio expired or could not be found.")
    return FileResponse(path, media_type="audio/mpeg", filename=file_name)


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
    output_name = "karaoke" if job.mode == "karaoke" and stem == "instrumental" else stem
    return FileResponse(path, media_type="audio/wav", filename=f"{safe_name}-{output_name}.wav")


@app.get("/jobs/{job_id}/share/{stem}")
def share_stem(job_id: str, stem: str) -> FileResponse:
    _cleanup_expired()
    if stem not in {"vocals", "drums", "instrumental"}:
        raise HTTPException(status_code=404, detail="Unknown stem.")
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job.status != "completed":
            raise HTTPException(status_code=404, detail="This output is not available.")
    path = _job_dir(job_id) / "outputs" / f"{stem}.mp3"
    if not path.exists():
        raise HTTPException(status_code=404, detail="This share file expired or could not be found.")
    safe_name = Path(job.source_name).stem or "song"
    output_name = "karaoke" if job.mode == "karaoke" and stem == "instrumental" else stem
    return FileResponse(path, media_type="audio/mpeg", filename=f"{safe_name}-{output_name}.mp3")


@app.get("/mixes/{mix_id}/files/{file_format}")
def download_mix(mix_id: str, file_format: str) -> FileResponse:
    _cleanup_expired()
    if file_format not in {"wav", "mp3"}:
        raise HTTPException(status_code=404, detail="Unknown mix format.")
    _validate_resource_id(mix_id, "mix")
    with jobs_lock:
        job = mixes.get(mix_id)
        if not job or job.status != "completed" or job.expires_at <= time.time():
            raise HTTPException(status_code=404, detail="This mix is not available.")
    path = _mix_dir(mix_id) / f"mix.{file_format}"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="This mix expired or could not be found.")
    media_type = "audio/wav" if file_format == "wav" else "audio/mpeg"
    return FileResponse(
        path,
        media_type=media_type,
        filename=f"stem-studio-mix.{file_format}",
        headers={"Cache-Control": "private, no-store"},
    )


async def _queue_practice(job_id: str) -> None:
    # Share the local model executor so separation and practice cannot compete
    # for memory on a MacBook or a single-worker portable container.
    executor.submit(process_practice, WORK_ROOT / "practice", job_id)


async def _practice_vocals(job_id: str) -> tuple[str, Path]:
    _validate_resource_id(job_id, "separation job")
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job.status != "completed" or job.expires_at <= time.time() or not job.vocals_url:
            raise HTTPException(status_code=404, detail="This vocal result is no longer available.")
        path = _job_dir(job_id) / "outputs" / "vocals.wav"
        name = job.source_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="This vocal result is no longer available.")
    return name, path


async def _practice_local_io() -> None:
    pass


register_practice_routes(
    app, root=lambda: WORK_ROOT / "practice", verify_token=_verify_upload_token,
    save_upload=_save_upload, queue_job=_queue_practice, completed_vocals=_practice_vocals,
    reload=_practice_local_io, commit=_practice_local_io,
)
