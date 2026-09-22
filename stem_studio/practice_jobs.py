"""Bounded, temporary singing analyses shared by local and Modal processors."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from stem_studio.audio import (
    SUPPORTED_MEDIA_EXTENSIONS,
    SUPPORTED_VIDEO_EXTENSIONS,
    probe_duration_seconds,
    run_demucs,
    transcode_audio_to_mp3,
)

TTL_SECONDS = 3600
MAX_QUEUED = 3
MAX_PROCESS_SECONDS = 660
MAX_QUEUE_SECONDS = 2100
ID_PATTERN = re.compile(r"[a-f0-9]{32}")
ERROR_MESSAGE = "This recording could not be analyzed. Try a clear solo vocal recording and a shorter section."
PUBLIC_FIELDS = (
    "id", "status", "stage", "progress", "created_at", "source_name", "input_kind",
    "source_start_seconds", "duration_seconds", "error", "result", "audio_url",
)
log = logging.getLogger(__name__)


class CompareRequest(BaseModel):
    take_job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    reference_job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    alignment: Literal["auto", "manual"] = "auto"
    offset_seconds: float = Field(default=0, ge=-90, le=90, allow_inf_nan=False)
    transpose_semitones: int = Field(default=0, ge=-24, le=24)


def write_record(root: Path, job_id: str, **updates: object) -> dict:
    directory = root / job_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "status.json"
    record = json.loads(path.read_text()) if path.exists() else {}
    record.update(updates)
    temporary = directory / f"status-{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(record, allow_nan=False))
    temporary.replace(path)
    return record


def read_record(root: Path, job_id: str) -> dict:
    if not ID_PATTERN.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="This practice session could not be found.")
    try:
        record = json.loads((root / job_id / "status.json").read_text())
        expires_at = float(record["expires_at"])
        if not math.isfinite(expires_at):
            raise ValueError("Invalid expiry")
        if expires_at <= time.time():
            raise HTTPException(status_code=410, detail="This practice session expired. Analyze the recording again.")
        return record
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail="This practice session could not be found.") from exc


def public_record(record: dict) -> dict:
    public = {key: record.get(key) for key in PUBLIC_FIELDS}
    return public | {"expires_in_seconds": max(0, round(float(record["expires_at"]) - time.time()))}


def expire_stalled(root: Path) -> bool:
    """Release queue slots after a worker timeout or a lost dispatch."""
    changed = False
    now = time.time()
    for path in root.glob("*/status.json"):
        try:
            record = json.loads(path.read_text())
            if record.get("status") == "processing":
                stalled = now - float(record.get("started_at", record["created_unix"])) > MAX_PROCESS_SECONDS
            else:
                stalled = record.get("status") == "queued" and now - float(record["created_unix"]) > MAX_QUEUE_SECONDS
            if stalled:
                write_record(root, path.parent.name, status="failed", progress=100,
                             stage="Analysis timed out", error="Analysis took too long. Try a shorter section.")
                (path.parent / "clip.wav").unlink(missing_ok=True)
                changed = True
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return changed


def cleanup_practice(root: Path) -> bool:
    changed = False
    now = time.time()
    for directory in root.glob("*"):
        if not directory.is_dir() or not ID_PATTERN.fullmatch(directory.name):
            continue
        try:
            record = json.loads((directory / "status.json").read_text())
            expires_at = float(record["expires_at"])
            expired = not math.isfinite(expires_at) or expires_at <= now
        except FileNotFoundError:
            # Do not race an upload that has not written its initial record yet.
            try:
                expired = now - directory.stat().st_mtime > TTL_SECONDS
            except OSError:
                continue
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            expired = True
        if expired:
            shutil.rmtree(directory, ignore_errors=True)
            changed = True
    return changed


def clip_audio(source: Path, output: Path, start: float, duration: float) -> None:
    """Decode only the requested section, before allocating model resources."""
    if source.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
        audio_stream = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index",
             "-of", "csv=p=0", str(source)],
            capture_output=True, text=True, timeout=30,
        )
        if audio_stream.returncode == 0 and not audio_stream.stdout.strip():
            raise ValueError("This video has no audio track. Choose a video with sound or an audio recording.")
    command = [
        "ffmpeg", "-y", "-v", "error", "-ss", str(start), "-i", str(source),
        "-t", str(duration), "-map", "0:a:0", "-vn", "-ac", "2", "-ar", "44100",
        "-c:a", "pcm_s16le", str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode or not output.is_file():
        raise ValueError("This audio section could not be read. Try an MP3 or WAV recording.")


def validate_controls(input_kind: str, start: float, duration: float, fmin: float, fmax: float) -> None:
    if input_kind not in {"isolated", "mixed"}:
        raise ValueError("Choose a solo vocal recording or a song with backing music.")
    if not all(math.isfinite(value) for value in (start, duration, fmin, fmax)):
        raise ValueError("Use finite numbers for the section and voice range.")
    if start < 0 or not 1 <= duration <= 90:
        raise ValueError("Choose a section between 1 and 90 seconds, starting at zero or later.")
    if not 40 <= fmin < fmax <= 1975:
        raise ValueError("Choose a voice range between 40 and 1975 Hz, with the low note below the high note.")


def process_practice(root: Path, job_id: str, *, device: str = "cpu",
                     commit: Callable[[], None] = lambda: None,
                     reload: Callable[[], None] = lambda: None,
                     commit_models: Callable[[], None] = lambda: None) -> None:
    """Worker entry point; web requests only clip, enqueue and read records."""
    reload()
    try:
        record = read_record(root, job_id)
    except HTTPException:
        return
    if record.get("status") != "queued":
        return
    directory = root / job_id

    class PracticeStopped(Exception):
        pass

    def update(**values: object) -> None:
        # Read the latest snapshot before every publication. Otherwise a warm
        # worker could overwrite a timeout published by the web container with
        # its older "processing" record and revive an abandoned analysis.
        reload()
        try:
            latest = read_record(root, job_id)
        except HTTPException as exc:
            raise PracticeStopped from exc
        if latest.get("status") not in {"queued", "processing"}:
            raise PracticeStopped
        write_record(root, job_id, **values)
        commit()

    try:
        update(status="processing", stage="Preparing your vocal", progress=8, started_at=time.time())
        from stem_studio.pitch import analyze_pitch

        # Keep work files outside the shared volume. Only the clip, result and
        # preview cross containers, so stale snapshots cannot erase work files.
        with tempfile.TemporaryDirectory(prefix="stem-practice-") as temp:
            work = Path(temp)
            source = work / "clip.wav"
            shutil.copy2(directory / "clip.wav", source)
            if record["input_kind"] == "mixed":
                update(stage="Isolating the vocal from this section", progress=15)
                source = run_demucs(source, work / "separated")["vocals"]
                commit_models()
            update(stage="Tracing your singing pitch", progress=40)
            last_progress = 39

            def report(fraction: float, message: str) -> None:
                nonlocal last_progress
                value = max(40, min(94, round(40 + float(fraction) * 54)))
                if value >= last_progress + 5:
                    update(stage=message, progress=value)
                    last_progress = value

            result = analyze_pitch(source, fmin=record["fmin"], fmax=record["fmax"],
                                   device=device, progress=report)
            if record["input_kind"] == "mixed":
                result.setdefault("warnings", []).append(
                    "Separated vocals may include harmonies or backing singers; use a solo vocal for the clearest trace."
                )
            update(stage="Preparing playback", progress=96)
            preview = transcode_audio_to_mp3(source, work / "audio.mp3")
            # A timeout may have been published while the worker was running.
            reload()
            latest = read_record(root, job_id)
            if latest.get("status") != "processing":
                return
            shutil.copy2(preview, directory / "audio.mp3")
            (directory / "clip.wav").unlink(missing_ok=True)
            # Publish file changes before update() reloads the volume snapshot.
            commit()
            update(status="completed", stage="Your pitch trace is ready", progress=100,
                   result=result, audio_url=f"/practice/jobs/{job_id}/audio",
                   expires_at=time.time() + TTL_SECONDS)
    except PracticeStopped:
        (directory / "clip.wav").unlink(missing_ok=True)
        commit()
    except Exception:
        log.exception("Practice analysis failed for job %s", job_id)
        (directory / "clip.wav").unlink(missing_ok=True)
        try:
            commit()
            update(status="failed", stage="Analysis could not finish", progress=100,
                   error=ERROR_MESSAGE, result=None, audio_url=None)
        except PracticeStopped:
            pass
        except (OSError, ValueError):
            log.exception("Could not write practice failure status")


def register_practice_routes(
    app: FastAPI, *, root: Callable[[], Path],
    verify_token: Callable[[str | None, str | None], None],
    save_upload: Callable[..., Awaitable[Path]],
    queue_job: Callable[[str], Awaitable[None]],
    completed_vocals: Callable[[str], Awaitable[tuple[str, Path]]],
    reload: Callable[[], Awaitable[None]],
    commit: Callable[[], Awaitable[None]],
) -> None:
    creation_lock = asyncio.Lock()

    async def maintenance() -> None:
        await reload()
        changed = expire_stalled(root())
        if cleanup_practice(root()) or changed:
            await commit()

    @app.post("/practice/jobs", status_code=202)
    async def create_practice_job(
        response: Response,
        file: Annotated[UploadFile | None, File()] = None,
        source_job_id: str | None = Form(default=None),
        input_kind: str = Form("isolated"),
        start_seconds: float = Form(0),
        duration_seconds: float = Form(15),
        fmin: float = Form(55), fmax: float = Form(1100),
        x_stem_timestamp: str | None = Header(default=None),
        x_stem_signature: str | None = Header(default=None),
    ) -> dict:
        response.headers["Cache-Control"] = "no-store"
        verify_token(x_stem_timestamp, x_stem_signature)
        reference = (source_job_id or "").strip()
        if (file is None) == (not reference):
            raise HTTPException(status_code=422, detail="Provide one recording or one completed vocal result.")
        try:
            validate_controls(input_kind, start_seconds, duration_seconds, fmin, fmax)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        async with creation_lock:
            await maintenance()
            pending = 0
            for path in root().glob("*/status.json"):
                try:
                    item = json.loads(path.read_text())
                    pending += item.get("status") in {"queued", "processing"}
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    continue
            if pending >= MAX_QUEUED:
                raise HTTPException(status_code=429, detail="Three practice sections are already queued. Try again when one finishes.")
            job_id = uuid.uuid4().hex
            directory = root() / job_id
            try:
                with tempfile.TemporaryDirectory(prefix="stem-practice-upload-") as temp:
                    temporary = Path(temp)
                    if file is not None:
                        source_name = Path(file.filename or "Recording").name[:160]
                        source = await save_upload(file, temporary / "upload", SUPPORTED_MEDIA_EXTENSIONS)
                    else:
                        source_name, existing_source = await completed_vocals(reference)
                        source = temporary / "vocals.wav"
                        await asyncio.to_thread(shutil.copy2, existing_source, source)
                        # Already separated audio should not run through Demucs twice.
                        input_kind = "isolated"
                    total = await asyncio.to_thread(probe_duration_seconds, source)
                    if not math.isfinite(total) or total <= 0 or total > 30 * 60:
                        raise ValueError("Choose an audio file that is 30 minutes or shorter.")
                    if start_seconds >= total:
                        raise ValueError("The section starts after the recording ends. Choose an earlier start.")
                    clipped_duration = min(duration_seconds, total - start_seconds)
                    if clipped_duration < 0.5:
                        raise ValueError("Keep at least half a second of audio in the selected section.")
                    clip = temporary / "clip.wav"
                    await asyncio.to_thread(clip_audio, source, clip, start_seconds, clipped_duration)
                    directory.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(shutil.copy2, clip, directory / "clip.wav")
                now = time.time()
                record = write_record(
                    root(), job_id, id=job_id, source_name=source_name, status="queued",
                    stage="Waiting for the pitch analyzer", progress=3,
                    created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    created_unix=now, expires_at=now + TTL_SECONDS,
                    input_kind=input_kind, source_start_seconds=start_seconds,
                    duration_seconds=round(clipped_duration, 3), fmin=fmin, fmax=fmax,
                    error=None, result=None, audio_url=None,
                )
                await commit()
            except HTTPException:
                shutil.rmtree(directory, ignore_errors=True)
                raise
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                shutil.rmtree(directory, ignore_errors=True)
                detail = str(exc) if isinstance(exc, ValueError) else "This audio file could not be read. Try an MP3 or WAV recording."
                raise HTTPException(status_code=422, detail=detail) from exc
            try:
                await queue_job(job_id)
            except Exception as exc:
                (directory / "clip.wav").unlink(missing_ok=True)
                write_record(root(), job_id, status="failed", progress=100,
                             stage="Could not start", error="The pitch analyzer could not start. Please try again.")
                await commit()
                raise HTTPException(status_code=503, detail="The pitch analyzer could not start. Please try again.") from exc
            return public_record(record)

    @app.get("/practice/jobs/{job_id}")
    async def get_practice_job(job_id: str, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        await reload()
        # Check the exact record first to distinguish expiry from an unknown id.
        read_record(root(), job_id)
        if expire_stalled(root()):
            await commit()
        return public_record(read_record(root(), job_id))

    @app.get("/practice/jobs/{job_id}/audio")
    async def get_practice_audio(job_id: str) -> FileResponse:
        await reload()
        record = read_record(root(), job_id)
        audio = root() / job_id / "audio.mp3"
        if record.get("status") != "completed" or not audio.is_file():
            raise HTTPException(status_code=404, detail="Playback is available when analysis finishes.")
        return FileResponse(audio, media_type="audio/mpeg", filename="singing-practice.mp3",
                            headers={"Cache-Control": "no-store"})

    @app.post("/practice/compare")
    async def compare_practice(
        request: CompareRequest,
        response: Response,
        x_stem_timestamp: str | None = Header(default=None),
        x_stem_signature: str | None = Header(default=None),
    ) -> dict:
        from stem_studio.pitch_compare import compare_pitch

        response.headers["Cache-Control"] = "no-store"
        verify_token(x_stem_timestamp, x_stem_signature)
        await reload()
        take = read_record(root(), request.take_job_id)
        reference = read_record(root(), request.reference_job_id)
        if any(job.get("status") != "completed" or not isinstance(job.get("result"), dict)
               for job in (take, reference)):
            raise HTTPException(status_code=409, detail="Wait until both pitch traces finish before comparing.")
        try:
            return await asyncio.to_thread(
                compare_pitch, take["result"], reference["result"], alignment=request.alignment,
                offset_seconds=request.offset_seconds, transpose_semitones=request.transpose_semitones,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
