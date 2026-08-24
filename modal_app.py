import asyncio
import hashlib
import hmac
import json
import os
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
UPLOAD_TOKEN_TTL_SECONDS = 300

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install_from_requirements("requirements-processor.txt")
    .env({"TORCH_HOME": str(MODEL_ROOT)})
    .add_local_python_source("stem_studio")
)
app = modal.App(APP_NAME)
data_volume = modal.Volume.from_name("stem-studio-jobs", create_if_missing=True)
model_volume = modal.Volume.from_name("stem-studio-models", create_if_missing=True)
upload_secret = modal.Secret.from_name("stem-studio-upload-secret")


def _job_dir(job_id: str) -> Path:
    return DATA_ROOT / "jobs" / job_id


def _status_path(job_id: str) -> Path:
    return _job_dir(job_id) / "status.json"


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


def _public_status(job: dict[str, object]) -> dict[str, object]:
    expires_at = float(job["expires_at"])
    return {
        key: value
        for key, value in job.items()
        if key not in {"expires_at", "call_id"}
    } | {"expires_in_seconds": max(0, round(expires_at - time.time()))}


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
def separate(job_id: str) -> None:
    from stem_studio.audio import run_demucs

    job_dir = _job_dir(job_id)
    source = next(job_dir.glob("source.*"))
    try:
        _write_status(job_id, status="processing", progress=8)
        data_volume.commit()
        with tempfile.TemporaryDirectory(prefix=f"stem-studio-{job_id[:8]}-") as temporary:
            outputs = run_demucs(source, Path(temporary) / "separated")
            _write_status(job_id, progress=90)
            output_dir = job_dir / "outputs"
            output_dir.mkdir(parents=True, exist_ok=True)
            urls: dict[str, str] = {}
            for stem, source_path in outputs.items():
                destination = output_dir / f"{stem}.wav"
                shutil.copy2(source_path, destination)
                urls[f"{stem}_url"] = f"/jobs/{job_id}/files/{stem}"
        source.unlink(missing_ok=True)
        _write_status(job_id, status="completed", progress=100, **urls)
        model_volume.commit()
        data_volume.commit()
    except Exception as exc:
        source.unlink(missing_ok=True)
        _write_status(job_id, status="failed", error=str(exc)[:1000])
        data_volume.commit()


@app.function(
    image=image,
    timeout=900,
    max_containers=1,
    scaledown_window=300,
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

    @web_app.get("/health")
    async def health():
        return {"status": "ok", "model": "htdemucs", "compute": "T4"}

    @web_app.post("/jobs", status_code=202)
    async def create_job(
        file: UploadFile = File(...),
        x_stem_timestamp: str | None = Header(default=None),
        x_stem_signature: str | None = Header(default=None),
    ):
        from stem_studio.audio import SUPPORTED_EXTENSIONS

        _verify_upload_token(x_stem_timestamp, x_stem_signature)
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise HTTPException(status_code=415, detail="Use MP3, WAV, M4A, FLAC, AAC, or OGG.")

        source_name = Path(file.filename or "audio").name
        job_id = uuid.uuid4().hex
        job_dir = _job_dir(job_id)
        source = await save_upload(file, job_dir, SUPPORTED_EXTENSIONS)

        now = time.time()
        status = _write_status(
            job_id,
            id=job_id,
            source_name=source_name,
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
        call = await separate.spawn.aio(job_id)
        _write_status(job_id, call_id=call.object_id)
        await data_volume.commit.aio()
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
        path = _job_dir(job_id) / "outputs" / f"{stem}.wav"
        if not path.exists():
            await data_volume.reload.aio()
        if not path.exists():
            raise HTTPException(status_code=404, detail="This output expired or could not be found.")
        status = json.loads(_status_path(job_id).read_text())
        safe_name = Path(str(status["source_name"])).stem or "song"
        return FileResponse(path, media_type="audio/wav", filename=f"{safe_name}-{stem}.wav")

    return web_app


@app.function(image=image, schedule=modal.Period(minutes=30), volumes={str(DATA_ROOT): data_volume})
def cleanup_expired() -> int:
    data_volume.reload()
    removed = 0
    jobs_root = DATA_ROOT / "jobs"
    for job_dir in jobs_root.glob("*") if jobs_root.exists() else []:
        try:
            status = json.loads((job_dir / "status.json").read_text())
            if float(status["expires_at"]) <= time.time():
                shutil.rmtree(job_dir)
                removed += 1
        except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
            continue
    if removed:
        data_volume.commit()
    return removed
