import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import modal

APP_NAME = "stem-studio-processor"
DATA_ROOT = Path("/data")
MODEL_ROOT = Path("/models")
MAX_UPLOAD_BYTES = 150 * 1024 * 1024
JOB_TTL_SECONDS = 3600
MAX_PENDING_JOBS = 3
UPLOAD_TOKEN_TTL_SECONDS = 300
YOUTUBE_IMPORT_TIMEOUT_SECONDS = 900
HOME_WORKER_LEASE_SECONDS = 300
HOME_WORKER_MAX_ATTEMPTS = 3
MAX_MIX_OFFSET_MS = 10 * 60 * 1000
MIN_MIX_GAIN_DB = -60.0
MAX_MIX_GAIN_DB = 24.0
SEPARATION_ERROR_MESSAGE = "The audio could not be separated. Check the file and try again."
MIX_ERROR_MESSAGE = "These tracks could not be mixed. Check both audio files and the timing controls."

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ca-certificates", "curl", "ffmpeg", "unzip")
    .run_commands(
        "curl -fsSL https://github.com/denoland/deno/releases/download/v2.9.5/deno-x86_64-unknown-linux-gnu.zip -o /tmp/deno.zip",
        "echo '8b010a3b1a4a0188a67cdb8a7a27348b2a501af78aec7fc74f2ace167368d530  /tmp/deno.zip' | sha256sum -c -",
        "unzip -q /tmp/deno.zip -d /usr/local/bin && rm /tmp/deno.zip && deno --version",
    )
    .pip_install_from_requirements("requirements-processor.txt")
    .env({"TORCH_HOME": str(MODEL_ROOT)})
    .add_local_python_source("stem_studio")
)
helper_api_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "fastapi>=0.115,<1",
        "python-multipart>=0.0.20,<1",
        "pydub>=0.25,<1",
    )
    .add_local_python_source("stem_studio")
)
app = modal.App(APP_NAME)
data_volume = modal.Volume.from_name("stem-studio-jobs", create_if_missing=True)
model_volume = modal.Volume.from_name("stem-studio-models", create_if_missing=True)
upload_secret = modal.Secret.from_name("stem-studio-upload-secret")
home_worker_secret = modal.Secret.from_name("stem-studio-home-worker-secret")

YOUTUBE_PUBLIC_FIELDS = (
    "id",
    "status",
    "progress",
    "created_at",
    "error",
    "title",
    "file_name",
    "file_url",
    "duration_seconds",
)
MIX_PUBLIC_FIELDS = (
    "id",
    "status",
    "created_at",
    "wav_url",
    "mp3_url",
)
JOB_ID_PATTERN = re.compile(r"[a-f0-9]{32}")


def _job_dir(job_id: str) -> Path:
    return DATA_ROOT / "jobs" / job_id


def _status_path(job_id: str) -> Path:
    return _job_dir(job_id) / "status.json"


def _youtube_import_dir(job_id: str) -> Path:
    return DATA_ROOT / "youtube-imports" / job_id


def _youtube_import_status_path(job_id: str) -> Path:
    return _youtube_import_dir(job_id) / "status.json"


def _youtube_import_cancel_path(job_id: str) -> Path:
    return _youtube_import_dir(job_id) / "cancelled"


def _mix_dir(mix_id: str) -> Path:
    return DATA_ROOT / "mixes" / mix_id


def _mix_status_path(mix_id: str) -> Path:
    return _mix_dir(mix_id) / "status.json"


def _write_status(job_id: str, **values: object) -> dict[str, object]:
    path = _status_path(job_id)
    current: dict[str, object] = {}
    if path.exists():
        current = json.loads(path.read_text())
    current.update(values)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(current))
    temporary.replace(path)
    return current


def _write_youtube_import_status(job_id: str, **values: object) -> dict[str, object]:
    path = _youtube_import_status_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, object] = {}
    if path.exists():
        current = json.loads(path.read_text())
    current.update(values)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(current))
    temporary.replace(path)
    return current


def _write_mix_status(mix_id: str, **values: object) -> dict[str, object]:
    path = _mix_status_path(mix_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, object] = {}
    if path.exists():
        current = json.loads(path.read_text())
    current.update(values)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(current))
    temporary.replace(path)
    return current


def _public_status(job: dict[str, object]) -> dict[str, object]:
    expires_at = float(job["expires_at"])
    public = {
        key: value
        for key, value in job.items()
        if key not in {"expires_at", "call_id"}
    }
    # Jobs created before Karaoke mode did not include this field.
    public.setdefault("mode", "stems")
    return public | {"expires_in_seconds": max(0, round(expires_at - time.time()))}


def _public_youtube_import_status(job: dict[str, object]) -> dict[str, object]:
    expires_at = float(job["expires_at"])
    # This is deliberately an allowlist: status records also contain the
    # canonical source URL and a private helper lease when cloud egress is
    # blocked. Neither may ever be returned to the browser.
    public = {key: job.get(key) for key in YOUTUBE_PUBLIC_FIELDS}
    if job.get("id") and _youtube_import_cancel_path(str(job["id"])).exists():
        public.update(status="failed", progress=100, error="YouTube import cancelled.")
    last_seen = job.get("helper_last_seen")
    public["helper_online"] = (
        isinstance(last_seen, (int, float)) and time.time() - float(last_seen) < HOME_WORKER_LEASE_SECONDS
    )
    return public | {"expires_in_seconds": max(0, round(expires_at - time.time()))}


def _public_mix_status(mix: dict[str, object]) -> dict[str, object]:
    expires_at = float(mix["expires_at"])
    public = {key: mix.get(key) for key in MIX_PUBLIC_FIELDS}
    return public | {"expires_in_seconds": max(0, round(expires_at - time.time()))}


def _validate_resource_id(value: str, resource_name: str) -> str:
    if not JOB_ID_PATTERN.fullmatch(value):
        raise ValueError(f"This {resource_name} could not be found.")
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
        raise ValueError("The mix controls contain an invalid number.")
    if abs(offset_ms) > MAX_MIX_OFFSET_MS:
        raise ValueError("Keep the vocal offset within 10 minutes.")
    if trim_start_seconds < 0 or trim_end_seconds < 0:
        raise ValueError("Vocal trim times cannot be negative.")
    if trim_end_seconds > 0 and trim_end_seconds <= trim_start_seconds:
        raise ValueError("Vocal trim end must be after trim start.")
    if not MIN_MIX_GAIN_DB <= vocal_gain_db <= MAX_MIX_GAIN_DB:
        raise ValueError("Keep vocal gain between -60 dB and +24 dB.")
    if not MIN_MIX_GAIN_DB <= instrumental_gain_db <= MAX_MIX_GAIN_DB:
        raise ValueError("Keep instrumental gain between -60 dB and +24 dB.")


def _youtube_import_timed_out(job: dict[str, object], now: float | None = None) -> bool:
    try:
        created_unix = float(job["created_unix"])
    except (KeyError, TypeError, ValueError):
        return False
    return (now or time.time()) - created_unix >= YOUTUBE_IMPORT_TIMEOUT_SECONDS


def _worker_lease_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _worker_lease_is_valid(job: dict[str, object], token: str, now: float | None = None) -> bool:
    expected = str(job.get("lease_token_hash") or "")
    try:
        lease_expires_at = float(job["lease_expires_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return bool(expected) and lease_expires_at > (now or time.time()) and hmac.compare_digest(
        expected,
        _worker_lease_hash(token),
    )


def _claim_home_worker_job(worker_id: str, now: float | None = None) -> dict[str, object] | None:
    current_time = now or time.time()
    imports_root = DATA_ROOT / "youtube-imports"
    candidates: list[tuple[float, Path, dict[str, object]]] = []
    for status_path in imports_root.glob("*/status.json") if imports_root.exists() else []:
        try:
            status = json.loads(status_path.read_text())
            if _youtube_import_cancel_path(str(status["id"])).exists():
                continue
            if float(status["expires_at"]) <= current_time:
                continue
            if status.get("status") == "processing_home":
                try:
                    lease_expired = float(status.get("lease_expires_at") or 0) <= current_time
                except (TypeError, ValueError):
                    lease_expired = True
                if lease_expired:
                    attempts = int(status.get("helper_attempts") or 0) + 1
                    if attempts >= HOME_WORKER_MAX_ATTEMPTS:
                        _write_youtube_import_status(
                            str(status["id"]),
                            status="failed",
                            progress=100,
                            error="The private helper stopped before finishing this import. Please try again.",
                            helper_attempts=attempts,
                            canonical_url=None,
                            lease_token_hash=None,
                            lease_expires_at=None,
                            worker_id=None,
                        )
                        continue
                    status = _write_youtube_import_status(
                        str(status["id"]),
                        status="waiting_for_helper",
                        progress=25,
                        helper_attempts=attempts,
                        lease_token_hash=None,
                        lease_expires_at=None,
                        worker_id=None,
                    )
            if status.get("status") != "waiting_for_helper" or not status.get("canonical_url"):
                continue
            candidates.append((float(status.get("created_unix") or current_time), status_path, status))
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    if not candidates:
        return None

    _, _, status = min(candidates, key=lambda item: item[0])
    token = secrets.token_urlsafe(32)
    job_id = str(status["id"])
    claimed = _write_youtube_import_status(
        job_id,
        status="processing_home",
        progress=35,
        error=None,
        lease_token_hash=_worker_lease_hash(token),
        lease_expires_at=current_time + HOME_WORKER_LEASE_SECONDS,
        helper_last_seen=current_time,
        worker_id=worker_id[:80],
    )
    return {
        "id": job_id,
        "url": str(claimed["canonical_url"]),
        "lease_token": token,
        "lease_seconds": HOME_WORKER_LEASE_SECONDS,
    }


def _verify_home_worker_secret(authorization: str | None) -> None:
    from fastapi import HTTPException

    expected = os.environ.get("HOME_WORKER_SECRET")
    supplied = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else ""
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="A valid private helper credential is required.")


def _verify_upload_token(timestamp: str | None, signature: str | None) -> None:
    from fastapi import HTTPException

    secret = os.environ.get("PROCESSOR_SHARED_SECRET")
    if not secret or not timestamp or not signature:
        raise HTTPException(status_code=401, detail="A valid upload token is required.")
    try:
        token_time = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="The upload token is invalid.") from exc
    if abs(int(time.time()) - token_time) > UPLOAD_TOKEN_TTL_SECONDS:
        raise HTTPException(status_code=401, detail="The upload token expired. Please retry.")
    expected = hmac.new(secret.encode(), f"{timestamp}:upload".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="The upload token is invalid.")


def _parse_segments(value: str) -> list[tuple[float, float | None]]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("The trim parts are invalid.") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError("Add at least one part to trim and merge.")
    if len(payload) > 50:
        raise ValueError("Use no more than 50 trim parts at a time.")
    result: list[tuple[float, float | None]] = []
    for index, part in enumerate(payload, start=1):
        if not isinstance(part, dict):
            raise ValueError(f"Part {index} is invalid.")
        start = part.get("start_seconds")
        end = part.get("end_seconds")
        if isinstance(start, bool) or not isinstance(start, (int, float)):
            raise ValueError(f"Part {index} has an invalid start time.")
        if end is not None and (isinstance(end, bool) or not isinstance(end, (int, float))):
            raise ValueError(f"Part {index} has an invalid end time.")
        result.append((float(start), None if end is None else float(end)))
    return result


@app.function(
    image=image,
    gpu="T4",
    timeout=1800,
    max_containers=1,
    scaledown_window=15,
    volumes={str(DATA_ROOT): data_volume, str(MODEL_ROOT): model_volume},
)
def separate(job_id: str, mode: str = "stems") -> None:
    if mode == "practice":
        from stem_studio.practice_jobs import process_practice

        process_practice(
            DATA_ROOT / "practice", job_id, device="cuda", commit=data_volume.commit,
            reload=data_volume.reload, commit_models=model_volume.commit,
        )
        return

    from stem_studio.audio import run_demucs, transcode_audio_to_mp3

    job_dir = _job_dir(job_id)
    source: Path | None = None
    try:
        # A warm GPU worker can retain an older Volume snapshot than the web
        # container that accepted this upload.
        data_volume.reload()
        status_path = _status_path(job_id)
        if not status_path.exists():
            return
        status = json.loads(status_path.read_text())
        if status.get("status") != "queued" or float(status["expires_at"]) <= time.time():
            return
        source = next(job_dir.glob("source.*"))
        _write_status(job_id, status="processing", progress=8)
        data_volume.commit()
        with tempfile.TemporaryDirectory(prefix=f"stem-studio-{job_id[:8]}-") as temporary:
            outputs = run_demucs(
                source,
                Path(temporary) / "separated",
                karaoke_only=mode == "karaoke",
            )
            _write_status(job_id, progress=90)
            output_dir = job_dir / "outputs"
            output_dir.mkdir(parents=True, exist_ok=True)
            urls: dict[str, str] = {}
            for stem, source_path in outputs.items():
                destination = output_dir / f"{stem}.wav"
                shutil.copy2(source_path, destination)
                try:
                    transcode_audio_to_mp3(destination, output_dir / f"{stem}.mp3")
                except (OSError, RuntimeError, ValueError):
                    # The WAV is the canonical result; the UI can fall back to it
                    # if the smaller convenience copy cannot be encoded.
                    pass
                urls[f"{stem}_url"] = f"/jobs/{job_id}/files/{stem}"
        source.unlink(missing_ok=True)
        _write_status(job_id, status="completed", progress=100, **urls)
        model_volume.commit()
        data_volume.commit()
    except Exception:
        if source is not None:
            source.unlink(missing_ok=True)
        try:
            _write_status(
                job_id,
                status="failed",
                progress=100,
                error=SEPARATION_ERROR_MESSAGE,
            )
            data_volume.commit()
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            pass


@app.function(
    image=image,
    timeout=900,
    max_containers=1,
    scaledown_window=15,
    volumes={str(DATA_ROOT): data_volume},
)
def import_youtube(job_id: str, canonical_url: str) -> None:
    from stem_studio.youtube import YouTubeHostedBlockError, YouTubeImportError, import_youtube_audio

    directory = _youtube_import_dir(job_id)
    # A warm worker keeps its own volume snapshot. Reload the queued record
    # committed by the web container before updating it.
    data_volume.reload()
    status_path = _youtube_import_status_path(job_id)
    if not status_path.exists() or _youtube_import_cancel_path(job_id).exists():
        return
    status = json.loads(status_path.read_text())
    if status.get("status") != "queued" or _youtube_import_timed_out(status):
        return
    try:
        _write_youtube_import_status(job_id, status="processing", progress=15)
        data_volume.commit()
        imported = import_youtube_audio(canonical_url, directory)
        # Publish the file, then reload status in case the web function marked
        # this worker as timed out while it was running.
        data_volume.commit()
        data_volume.reload()
        if not status_path.exists() or _youtube_import_cancel_path(job_id).exists():
            return
        status = json.loads(status_path.read_text())
        if status.get("status") not in {"queued", "processing"} or _youtube_import_timed_out(status):
            imported.path.unlink(missing_ok=True)
            data_volume.commit()
            return
        _write_youtube_import_status(
            job_id,
            status="completed",
            progress=100,
            title=imported.title,
            file_name=imported.download_name,
            file_url=f"/imports/youtube/{job_id}/file",
            duration_seconds=round(imported.duration_seconds, 3),
        )
        data_volume.commit()
    except YouTubeHostedBlockError:
        for path in directory.glob("source.*"):
            path.unlink(missing_ok=True)
        data_volume.reload()
        if not status_path.exists() or _youtube_import_cancel_path(job_id).exists():
            return
        latest = json.loads(status_path.read_text())
        if latest.get("status") not in {"queued", "processing"}:
            return
        _write_youtube_import_status(
            job_id,
            status="waiting_for_helper",
            progress=25,
            error=None,
            canonical_url=canonical_url,
            cloud_outcome="host_blocked",
            helper_attempts=0,
        )
        data_volume.commit()
    except YouTubeImportError as exc:
        for path in directory.glob("source.*"):
            path.unlink(missing_ok=True)
        data_volume.reload()
        if not status_path.exists() or _youtube_import_cancel_path(job_id).exists():
            return
        latest = json.loads(status_path.read_text())
        if latest.get("status") not in {"queued", "processing"}:
            return
        _write_youtube_import_status(job_id, status="failed", error=str(exc), progress=100)
        data_volume.commit()
    except Exception:
        for path in directory.glob("source.*"):
            path.unlink(missing_ok=True)
        data_volume.reload()
        if not status_path.exists() or _youtube_import_cancel_path(job_id).exists():
            return
        latest = json.loads(status_path.read_text())
        if latest.get("status") not in {"queued", "processing"}:
            return
        _write_youtube_import_status(
            job_id,
            status="failed",
            error="YouTube audio import failed. Please try again.",
            progress=100,
        )
        data_volume.commit()


@app.function(
    image=image,
    timeout=900,
    max_containers=1,
    scaledown_window=30,
    secrets=[upload_secret],
    volumes={str(DATA_ROOT): data_volume},
)
@modal.concurrent(max_inputs=8)
@modal.asgi_app()
def web():
    from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask

    async def save_upload(file: UploadFile, directory: Path, allowed_extensions: set[str]) -> Path:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in allowed_extensions:
            raise HTTPException(status_code=415, detail="This file format is not supported for that action.")
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / f"source{suffix}"
        size = 0
        try:
            with source.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="Choose a file smaller than 150 MB.")
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

    web_app = FastAPI(title="Stem Studio Processor", version="1.0.0")
    web_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Stem-Timestamp", "X-Stem-Signature"],
    )
    separation_creation_lock = asyncio.Lock()
    youtube_import_creation_lock = asyncio.Lock()

    async def completed_stem_job(job_id: str, stem: str) -> tuple[dict[str, object], Path]:
        try:
            _validate_resource_id(job_id, "separation job")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await data_volume.reload.aio()
        status_path = _status_path(job_id)
        path = _job_dir(job_id) / "outputs" / f"{stem}.wav"
        if not status_path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="This output expired or could not be found.")
        try:
            status = json.loads(status_path.read_text())
            available = (
                float(status["expires_at"]) > time.time()
                and status.get("status") == "completed"
                and bool(status.get(f"{stem}_url"))
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            available = False
            status = {}
        if not available:
            raise HTTPException(status_code=404, detail="This output is not available.")
        return status, path

    @web_app.get("/health")
    async def health():
        return {"status": "ok", "model": "htdemucs", "compute": "T4"}

    @web_app.post("/jobs", status_code=202)
    async def create_job(
        file: UploadFile = File(...),
        mode: str = Form("stems"),
        x_stem_timestamp: str | None = Header(default=None),
        x_stem_signature: str | None = Header(default=None),
    ):
        from stem_studio.audio import MAX_SEPARATION_DURATION_SECONDS, SUPPORTED_EXTENSIONS, probe_duration_seconds

        _verify_upload_token(x_stem_timestamp, x_stem_signature)
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise HTTPException(status_code=415, detail="Use MP3, WAV, M4A, FLAC, AAC, OGG, or WEBM audio.")
        if mode not in {"stems", "karaoke"}:
            raise HTTPException(status_code=422, detail="Choose either stems or karaoke mode.")

        source_name = Path(file.filename or "audio").name
        async with separation_creation_lock:
            await data_volume.reload.aio()
            jobs_root = DATA_ROOT / "jobs"
            active_jobs = 0
            for status_path in jobs_root.glob("*/status.json") if jobs_root.exists() else []:
                try:
                    existing = json.loads(status_path.read_text())
                    if (
                        float(existing["expires_at"]) > time.time()
                        and existing.get("status") in {"queued", "processing"}
                    ):
                        active_jobs += 1
                except (FileNotFoundError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                    continue
            if active_jobs >= MAX_PENDING_JOBS:
                await file.close()
                raise HTTPException(status_code=429, detail="The processor is busy. Please try again shortly.")

            job_id = uuid.uuid4().hex
            job_dir = _job_dir(job_id)
            source = await save_upload(file, job_dir, SUPPORTED_EXTENSIONS)
            try:
                duration = await asyncio.to_thread(probe_duration_seconds, source)
            except (OSError, RuntimeError, ValueError) as exc:
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=422,
                    detail="This audio file could not be read. Try converting it to MP3 or WAV.",
                ) from exc
            if duration > MAX_SEPARATION_DURATION_SECONDS:
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=422,
                    detail="Choose an audio file that is 30 minutes or shorter for separation.",
                )
            now = time.time()
            status = _write_status(
                job_id,
                id=job_id,
                source_name=source_name,
                mode=mode,
                status="queued",
                progress=3,
                created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                expires_at=now + JOB_TTL_SECONDS,
                error=None,
                vocals_url=None,
                drums_url=None,
                instrumental_url=None,
            )
            await data_volume.commit.aio()
            try:
                await separate.spawn.aio(job_id, mode)
            except Exception as exc:
                source.unlink(missing_ok=True)
                status = _write_status(
                    job_id,
                    status="failed",
                    progress=100,
                    error="The separation worker could not start. Please try again shortly.",
                )
                await data_volume.commit.aio()
                raise HTTPException(
                    status_code=503,
                    detail="The separation worker could not start. Please try again shortly.",
                ) from exc
        return _public_status(status)

    @web_app.post("/tools/extract-mp3")
    async def extract_mp3(
        file: UploadFile = File(...),
        x_stem_timestamp: str | None = Header(default=None),
        x_stem_signature: str | None = Header(default=None),
    ):
        from stem_studio.audio import SUPPORTED_VIDEO_EXTENSIONS, extract_audio_to_mp3

        _verify_upload_token(x_stem_timestamp, x_stem_signature)
        source_name = Path(file.filename or "video").name
        directory = Path(tempfile.mkdtemp(prefix="stem-studio-extract-"))
        source = await save_upload(file, directory, SUPPORTED_VIDEO_EXTENSIONS)
        output = directory / f"{Path(source_name).stem or 'video'}.mp3"
        try:
            await asyncio.to_thread(extract_audio_to_mp3, source, output)
        except (OSError, RuntimeError, ValueError) as exc:
            shutil.rmtree(directory, ignore_errors=True)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return FileResponse(
            output,
            media_type="audio/mpeg",
            filename=output.name,
            background=BackgroundTask(shutil.rmtree, directory, ignore_errors=True),
        )

    @web_app.post("/tools/trim-merge")
    async def trim_merge(
        file: UploadFile = File(...),
        segments: str = Form(...),
        x_stem_timestamp: str | None = Header(default=None),
        x_stem_signature: str | None = Header(default=None),
    ):
        from stem_studio.audio import SUPPORTED_EXTENSIONS, trim_and_merge_audio

        _verify_upload_token(x_stem_timestamp, x_stem_signature)
        try:
            parsed_segments = _parse_segments(segments)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        source_name = Path(file.filename or "audio").name
        directory = Path(tempfile.mkdtemp(prefix="stem-studio-trim-"))
        source = await save_upload(file, directory, SUPPORTED_EXTENSIONS)
        output = directory / f"{Path(source_name).stem or 'audio'}-trimmed.mp3"
        try:
            await asyncio.to_thread(trim_and_merge_audio, source, parsed_segments, output)
        except (OSError, RuntimeError, ValueError) as exc:
            shutil.rmtree(directory, ignore_errors=True)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return FileResponse(
            output,
            media_type="audio/mpeg",
            filename=output.name,
            background=BackgroundTask(shutil.rmtree, directory, ignore_errors=True),
        )

    @web_app.post("/tools/mix")
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
    ):
        from stem_studio.audio import SUPPORTED_EXTENSIONS, mix_tracks

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
        except ValueError as exc:
            if instrumental is not None:
                await instrumental.close()
            await vocal.close()
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if reference_id is not None:
            try:
                _, instrumental_source = await completed_stem_job(reference_id, "instrumental")
            except HTTPException:
                await vocal.close()
                raise
        else:
            instrumental_source = Path()

        mix_id = uuid.uuid4().hex
        mix_directory = _mix_dir(mix_id)
        try:
            if instrumental is not None:
                instrumental_source = await save_upload(
                    instrumental,
                    mix_directory / "instrumental-input",
                    SUPPORTED_EXTENSIONS,
                )
            vocal_source = await save_upload(
                vocal,
                mix_directory / "vocal-input",
                SUPPORTED_EXTENSIONS,
            )
            wav_path, mp3_path = await asyncio.to_thread(
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
            status = _write_mix_status(
                mix_id,
                id=mix_id,
                status="completed",
                created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                expires_at=now + JOB_TTL_SECONDS,
                wav_url=f"/mixes/{mix_id}/files/wav",
                mp3_url=f"/mixes/{mix_id}/files/mp3",
            )
            await data_volume.commit.aio()
        except HTTPException:
            shutil.rmtree(mix_directory, ignore_errors=True)
            await data_volume.commit.aio()
            raise
        except ValueError as exc:
            shutil.rmtree(mix_directory, ignore_errors=True)
            await data_volume.commit.aio()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            shutil.rmtree(mix_directory, ignore_errors=True)
            await data_volume.commit.aio()
            raise HTTPException(status_code=422, detail=MIX_ERROR_MESSAGE) from exc
        return _public_mix_status(status)

    @web_app.post("/imports/youtube", status_code=202)
    async def create_youtube_import(
        url: str = Form(...),
        rights_confirmed: bool = Form(False),
        x_stem_timestamp: str | None = Header(default=None),
        x_stem_signature: str | None = Header(default=None),
    ):
        from stem_studio.youtube import YouTubeUrlError, canonicalize_youtube_url

        _verify_upload_token(x_stem_timestamp, x_stem_signature)
        if not rights_confirmed:
            raise HTTPException(status_code=422, detail="Confirm that you own this audio or have permission to download it.")
        try:
            canonical_url = canonicalize_youtube_url(url)
        except YouTubeUrlError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        async with youtube_import_creation_lock:
            await data_volume.reload.aio()
            imports_root = DATA_ROOT / "youtube-imports"
            active_imports = 0
            for status_path in imports_root.glob("*/status.json") if imports_root.exists() else []:
                try:
                    existing_status = json.loads(status_path.read_text())
                    if _youtube_import_cancel_path(str(existing_status["id"])).exists():
                        continue
                    if (
                        float(existing_status["expires_at"]) > time.time()
                        and existing_status.get("status")
                        in {"queued", "processing", "waiting_for_helper", "processing_home"}
                        and (
                            existing_status.get("status") not in {"queued", "processing"}
                            or not _youtube_import_timed_out(existing_status)
                        )
                    ):
                        active_imports += 1
                except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
            if active_imports >= 1:
                raise HTTPException(
                    status_code=429,
                    detail="A YouTube import is already running. Please try again when it finishes.",
                )

            job_id = uuid.uuid4().hex
            now = time.time()
            status = _write_youtube_import_status(
                job_id,
                id=job_id,
                status="queued",
                progress=5,
                created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                created_unix=now,
                expires_at=now + JOB_TTL_SECONDS,
                error=None,
                title=None,
                file_name=None,
                file_url=None,
                duration_seconds=None,
            )
            await data_volume.commit.aio()
            try:
                await import_youtube.spawn.aio(job_id, canonical_url)
            except Exception as exc:
                status = _write_youtube_import_status(
                    job_id,
                    status="failed",
                    progress=100,
                    error="The YouTube importer could not start. Please try again shortly.",
                )
                await data_volume.commit.aio()
                raise HTTPException(
                    status_code=503,
                    detail="The YouTube importer could not start. Please try again shortly.",
                ) from exc
        return _public_youtube_import_status(status)

    @web_app.get("/imports/youtube/{job_id}")
    async def get_youtube_import(job_id: str):
        await data_volume.reload.aio()
        path = _youtube_import_status_path(job_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="This YouTube import expired or could not be found.")
        status = json.loads(path.read_text())
        if float(status["expires_at"]) <= time.time():
            raise HTTPException(status_code=404, detail="This YouTube import expired or could not be found.")
        if status.get("status") in {"queued", "processing"} and _youtube_import_timed_out(status):
            status = _write_youtube_import_status(
                job_id,
                status="failed",
                progress=100,
                error="This YouTube import exceeded its processing window. Please try again.",
            )
            await data_volume.commit.aio()
        return _public_youtube_import_status(status)

    @web_app.post("/imports/youtube/{job_id}/cancel")
    async def cancel_youtube_import(
        job_id: str,
        x_stem_timestamp: str | None = Header(default=None),
        x_stem_signature: str | None = Header(default=None),
    ):
        _verify_upload_token(x_stem_timestamp, x_stem_signature)
        async with youtube_import_creation_lock:
            await data_volume.reload.aio()
            status_path = _youtube_import_status_path(job_id)
            if not status_path.exists():
                raise HTTPException(status_code=404, detail="This YouTube import expired or could not be found.")
            status = json.loads(status_path.read_text())
            if status.get("status") in {"queued", "processing", "waiting_for_helper", "processing_home"}:
                _youtube_import_cancel_path(job_id).touch(exist_ok=True)
                status = _write_youtube_import_status(
                    job_id,
                    status="failed",
                    progress=100,
                    error="YouTube import cancelled.",
                    canonical_url=None,
                    lease_token_hash=None,
                    lease_expires_at=None,
                )
                await data_volume.commit.aio()
        return _public_youtube_import_status(status)

    @web_app.get("/imports/youtube/{job_id}/file")
    async def download_youtube_import(job_id: str):
        await data_volume.reload.aio()
        status_path = _youtube_import_status_path(job_id)
        output = _youtube_import_dir(job_id) / "source.mp3"
        if not status_path.exists() or _youtube_import_cancel_path(job_id).exists() or not output.is_file():
            raise HTTPException(status_code=404, detail="This imported audio expired or could not be found.")
        status = json.loads(status_path.read_text())
        if (
            float(status["expires_at"]) <= time.time()
            or status.get("status") != "completed"
            or not status.get("file_name")
        ):
            raise HTTPException(status_code=404, detail="This imported audio is not available.")
        return FileResponse(output, media_type="audio/mpeg", filename=str(status["file_name"]))

    @web_app.get("/jobs/{job_id}")
    async def get_job(job_id: str):
        await data_volume.reload.aio()
        path = _status_path(job_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="This job expired or could not be found.")
        status = json.loads(path.read_text())
        if float(status["expires_at"]) <= time.time():
            raise HTTPException(status_code=404, detail="This job expired or could not be found.")
        return _public_status(status)

    @web_app.get("/jobs/{job_id}/files/{stem}")
    async def download_stem(job_id: str, stem: str):
        if stem not in {"vocals", "drums", "instrumental"}:
            raise HTTPException(status_code=404, detail="Unknown stem.")
        status, path = await completed_stem_job(job_id, stem)
        safe_name = Path(str(status["source_name"])).stem or "song"
        output_name = "karaoke" if status.get("mode") == "karaoke" and stem == "instrumental" else stem
        return FileResponse(
            path,
            media_type="audio/wav",
            filename=f"{safe_name}-{output_name}.wav",
            headers={"Cache-Control": "private, no-store"},
        )

    @web_app.get("/jobs/{job_id}/share/{stem}")
    async def share_stem(job_id: str, stem: str):
        if stem not in {"vocals", "drums", "instrumental"}:
            raise HTTPException(status_code=404, detail="Unknown stem.")
        status, _ = await completed_stem_job(job_id, stem)
        path = _job_dir(job_id) / "outputs" / f"{stem}.mp3"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="This share file expired or could not be found.")
        safe_name = Path(str(status["source_name"])).stem or "song"
        output_name = "karaoke" if status.get("mode") == "karaoke" and stem == "instrumental" else stem
        return FileResponse(
            path,
            media_type="audio/mpeg",
            filename=f"{safe_name}-{output_name}.mp3",
            headers={"Cache-Control": "private, no-store"},
        )

    @web_app.get("/mixes/{mix_id}/files/{file_format}")
    async def download_mix(mix_id: str, file_format: str):
        if file_format not in {"wav", "mp3"}:
            raise HTTPException(status_code=404, detail="Unknown mix format.")
        try:
            _validate_resource_id(mix_id, "mix")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await data_volume.reload.aio()
        status_path = _mix_status_path(mix_id)
        path = _mix_dir(mix_id) / f"mix.{file_format}"
        if not status_path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="This mix expired or could not be found.")
        try:
            status = json.loads(status_path.read_text())
            available = float(status["expires_at"]) > time.time() and status.get("status") == "completed"
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            available = False
        if not available:
            raise HTTPException(status_code=404, detail="This mix is not available.")
        media_type = "audio/wav" if file_format == "wav" else "audio/mpeg"
        return FileResponse(
            path,
            media_type=media_type,
            filename=f"stem-studio-mix.{file_format}",
            headers={"Cache-Control": "private, no-store"},
        )

    from stem_studio.practice_jobs import register_practice_routes

    async def queue_practice(job_id: str) -> None:
        # One GPU container serves both separation and pitch analysis, keeping
        # their total concurrency at one even when both tabs submit work.
        await separate.spawn.aio(job_id, "practice")

    async def practice_vocals(job_id: str) -> tuple[str, Path]:
        status, path = await completed_stem_job(job_id, "vocals")
        return str(status.get("source_name", "Separated vocal")), path

    register_practice_routes(
        web_app, root=lambda: DATA_ROOT / "practice", verify_token=_verify_upload_token,
        save_upload=save_upload, queue_job=queue_practice, completed_vocals=practice_vocals,
        reload=data_volume.reload.aio, commit=data_volume.commit.aio,
    )
    return web_app


@app.function(
    image=helper_api_image,
    timeout=900,
    max_containers=1,
    scaledown_window=2,
    secrets=[home_worker_secret],
    volumes={str(DATA_ROOT): data_volume},
)
@modal.concurrent(max_inputs=4)
@modal.asgi_app()
def home_worker_api():
    """Small, low-idle-cost broker used only by the private home helper."""
    from fastapi import FastAPI, File, Form, Header, HTTPException, Response, UploadFile

    from stem_studio.audio import probe_duration_seconds
    from stem_studio.youtube import (
        MAX_YOUTUBE_DURATION_SECONDS,
        MAX_YOUTUBE_MP3_BYTES,
        safe_youtube_download_name,
    )

    helper_app = FastAPI(title="Stem Studio Private Helper Broker", version="1.0.0")
    mutation_lock = asyncio.Lock()

    async def require_job_and_lease(job_id: str, lease_token: str | None) -> dict[str, object]:
        if not lease_token:
            raise HTTPException(status_code=401, detail="The helper lease is missing.")
        status_path = _youtube_import_status_path(job_id)
        if not status_path.exists() or _youtube_import_cancel_path(job_id).exists():
            raise HTTPException(status_code=404, detail="This helper job expired or could not be found.")
        status = json.loads(status_path.read_text())
        if status.get("status") != "processing_home" or not _worker_lease_is_valid(status, lease_token):
            raise HTTPException(status_code=409, detail="This helper lease expired or was replaced.")
        return status

    @helper_app.get("/health")
    async def helper_health():
        return {"status": "ok", "service": "stem-studio-home-helper"}

    @helper_app.post("/worker/youtube/claim")
    async def claim_youtube_job(
        worker_id: str = Form("private-helper"),
        authorization: str | None = Header(default=None),
    ):
        _verify_home_worker_secret(authorization)
        async with mutation_lock:
            await data_volume.reload.aio()
            claim = _claim_home_worker_job(worker_id)
            # Claim reconciliation can also expire a dead lease and mark the
            # final allowed attempt failed, even when there is nothing to
            # return to this poll.
            await data_volume.commit.aio()
            if claim is None:
                return Response(status_code=204)
        return claim

    @helper_app.post("/worker/youtube/{job_id}/heartbeat")
    async def heartbeat_youtube_job(
        job_id: str,
        authorization: str | None = Header(default=None),
        x_worker_lease: str | None = Header(default=None),
    ):
        _verify_home_worker_secret(authorization)
        async with mutation_lock:
            await data_volume.reload.aio()
            await require_job_and_lease(job_id, x_worker_lease)
            now = time.time()
            _write_youtube_import_status(
                job_id,
                lease_expires_at=now + HOME_WORKER_LEASE_SECONDS,
                helper_last_seen=now,
            )
            await data_volume.commit.aio()
        return {"status": "ok", "lease_seconds": HOME_WORKER_LEASE_SECONDS}

    @helper_app.post("/worker/youtube/{job_id}/complete")
    async def complete_youtube_job(
        job_id: str,
        file: UploadFile = File(...),
        title: str = Form("YouTube audio"),
        sha256: str = Form(...),
        authorization: str | None = Header(default=None),
        x_worker_lease: str | None = Header(default=None),
    ):
        _verify_home_worker_secret(authorization)
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise HTTPException(status_code=422, detail="The helper file checksum is invalid.")

        temporary_directory = Path(tempfile.mkdtemp(prefix="stem-studio-helper-upload-"))
        temporary_output = temporary_directory / "source.mp3"
        digest = hashlib.sha256()
        size = 0
        try:
            try:
                with temporary_output.open("wb") as destination:
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_YOUTUBE_MP3_BYTES:
                            raise HTTPException(status_code=413, detail="The helper MP3 is too large.")
                        digest.update(chunk)
                        destination.write(chunk)
            except Exception:
                shutil.rmtree(temporary_directory, ignore_errors=True)
                raise
        finally:
            await file.close()
        if size == 0 or not hmac.compare_digest(digest.hexdigest(), sha256):
            shutil.rmtree(temporary_directory, ignore_errors=True)
            raise HTTPException(status_code=422, detail="The helper MP3 was incomplete.")
        try:
            duration = await asyncio.to_thread(probe_duration_seconds, temporary_output)
        except (OSError, RuntimeError, ValueError) as exc:
            shutil.rmtree(temporary_directory, ignore_errors=True)
            raise HTTPException(status_code=422, detail="The helper did not return a valid MP3.") from exc
        if duration > MAX_YOUTUBE_DURATION_SECONDS + 1:
            shutil.rmtree(temporary_directory, ignore_errors=True)
            raise HTTPException(
                status_code=422,
                detail=f"Choose a YouTube video that is {MAX_YOUTUBE_DURATION_SECONDS // 60} minutes or shorter.",
            )

        safe_title, download_name = safe_youtube_download_name(title)
        try:
            async with mutation_lock:
                await data_volume.reload.aio()
                await require_job_and_lease(job_id, x_worker_lease)
                directory = _youtube_import_dir(job_id)
                directory.mkdir(parents=True, exist_ok=True)
                shutil.copy2(temporary_output, directory / "source.mp3")
                completed_at = time.time()
                status = _write_youtube_import_status(
                    job_id,
                    status="completed",
                    progress=100,
                    error=None,
                    title=safe_title,
                    file_name=download_name,
                    file_url=f"/imports/youtube/{job_id}/file",
                    duration_seconds=round(duration, 3),
                    result_sha256=sha256,
                    completed_via="home_helper",
                    canonical_url=None,
                    lease_token_hash=None,
                    lease_expires_at=None,
                    expires_at=completed_at + JOB_TTL_SECONDS,
                )
                await data_volume.commit.aio()
        finally:
            shutil.rmtree(temporary_directory, ignore_errors=True)
        return _public_youtube_import_status(status)

    @helper_app.post("/worker/youtube/{job_id}/fail")
    async def fail_youtube_job(
        job_id: str,
        code: str = Form("temporary"),
        authorization: str | None = Header(default=None),
        x_worker_lease: str | None = Header(default=None),
    ):
        _verify_home_worker_secret(authorization)
        terminal_errors = {
            "restricted": "This YouTube video is restricted or unavailable. Try a public video you are allowed to download.",
            "too_long": f"Choose a YouTube video that is {MAX_YOUTUBE_DURATION_SECONDS // 60} minutes or shorter.",
            "too_large": "This YouTube audio is too large to import safely.",
            "invalid_audio": "YouTube did not return usable audio for this video.",
            "invalid_url": "This is not a supported single-video YouTube link.",
        }
        async with mutation_lock:
            await data_volume.reload.aio()
            status = await require_job_and_lease(job_id, x_worker_lease)
            attempts = int(status.get("helper_attempts") or 0) + 1
            if code in terminal_errors or attempts >= HOME_WORKER_MAX_ATTEMPTS:
                error = terminal_errors.get(
                    code,
                    "YouTube also blocked or interrupted the private helper. Upload the audio file directly instead.",
                )
                updated = _write_youtube_import_status(
                    job_id,
                    status="failed",
                    progress=100,
                    error=error,
                    helper_attempts=attempts,
                    canonical_url=None,
                    lease_token_hash=None,
                    lease_expires_at=None,
                )
            else:
                updated = _write_youtube_import_status(
                    job_id,
                    status="waiting_for_helper",
                    progress=25,
                    error=None,
                    helper_attempts=attempts,
                    lease_token_hash=None,
                    lease_expires_at=None,
                    worker_id=None,
                )
            await data_volume.commit.aio()
        return _public_youtube_import_status(updated)

    return helper_app


@app.function(image=image, schedule=modal.Period(minutes=30), volumes={str(DATA_ROOT): data_volume})
def cleanup_expired() -> int:
    data_volume.reload()
    removed = 0

    def cleanup_root(root: Path) -> None:
        nonlocal removed
        for directory in root.glob("*") if root.exists() else []:
            status_path = directory / "status.json"
            try:
                status = json.loads(status_path.read_text())
                should_remove = float(status["expires_at"]) <= time.time()
            except FileNotFoundError:
                # A web request may still be streaming an upload into a newly
                # created directory that does not have a status record yet.
                continue
            except OSError:
                continue
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                # A corrupt status record can never become a usable job and
                # must not retain private audio indefinitely.
                should_remove = True
            if should_remove:
                shutil.rmtree(directory, ignore_errors=True)
                if not directory.exists():
                    removed += 1

    cleanup_root(DATA_ROOT / "jobs")
    cleanup_root(DATA_ROOT / "youtube-imports")
    cleanup_root(DATA_ROOT / "mixes")
    cleanup_root(DATA_ROOT / "practice")
    if removed:
        data_volume.commit()
    return removed
