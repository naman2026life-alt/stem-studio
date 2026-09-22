"""Opt-in, real-model smoke test for the private Mac processor only.

Run from the repository: .venv/bin/python scripts/verify_mac.py
Requires the processor on 127.0.0.1:8766 and its existing Keychain credential.
Uses generated audio/video, never Modal or copyrighted media. Does not change
services, Keychain, environment files, or production configuration. Completed
test results use the processor's normal one-hour retention.
"""

from __future__ import annotations

import array
import hashlib
import hmac
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit
import wave

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stem_studio.mac_service import load_secret  # noqa: E402

BASE_URL = "http://127.0.0.1:8766"
CASE_TIMEOUT_SECONDS = 300
TOTAL_TIMEOUT_SECONDS = 900
RATE = 16000


def generated_vocal(*, delay: float = 0, cents: float = 0, silence: bool = False,
                    duration: float | None = None) -> bytes:
    """Deterministic harmonic melody with stronger second harmonic and breaths."""
    pitches = [57, 61, 59, 64, 62, 66, 64, 69]
    samples = array.array("h")
    phase = 0.0
    for index in range(round((duration if duration is not None else 6.8 + delay) * RATE)):
        moment = index / RATE - delay - 0.2
        note = math.floor(moment / 0.8)
        local = moment - note * 0.8
        voiced = not silence and 0 <= note < len(pitches) and local < 0.7
        if voiced:
            hz = 440 * 2 ** ((pitches[note] - 69 + cents / 100) / 12)
            phase += 2 * math.pi * hz / RATE
            envelope = min(1.0, local / 0.025, (0.7 - local) / 0.025)
            value = envelope * (0.20 * math.sin(phase) + 0.32 * math.sin(2 * phase)
                                + 0.16 * math.sin(3 * phase) + 0.08 * math.sin(4 * phase))
        else:
            value = 0.0
        samples.append(round(max(-0.95, min(0.95, value)) * 32767))
    if sys.byteorder != "little":
        samples.byteswap()
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(samples.tobytes())
    return output.getvalue()


def ffmpeg(arguments: list[str], content: bytes) -> bytes:
    result = subprocess.run(["ffmpeg", "-v", "error", *arguments], input=content,
                            capture_output=True, timeout=60, check=False)
    require(result.returncode == 0 and bool(result.stdout), "Generated media conversion failed.")
    return result.stdout


def generated_video(audio: bytes) -> bytes:
    return ffmpeg([
        "-f", "lavfi", "-i", "color=c=black:s=160x120:r=10:d=3",
        "-f", "wav", "-i", "pipe:0", "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", "-movflags", "frag_keyframe+empty_moov",
        "-f", "mp4", "pipe:1",
    ], audio)


def decoded_duration(content: bytes) -> float:
    samples = ffmpeg(["-i", "pipe:0", "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(RATE),
                      "-f", "f32le", "pipe:1"], content)
    return len(samples) / 4 / RATE


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def local_path(value: str) -> str:
    parsed = urlsplit(value)
    require(value.startswith("/") and not value.startswith("//") and "\\" not in value
            and not parsed.scheme and not parsed.netloc, "Processor returned a non-local output URL.")
    return value


def report(**values: object) -> None:
    print(json.dumps(values, allow_nan=False), flush=True)


def verify(secret: str) -> dict:
    started = time.monotonic()
    total_deadline = started + TOTAL_TIMEOUT_SECONDS

    def headers() -> dict[str, str]:
        timestamp = str(int(time.time()))
        signature = hmac.new(secret.encode(), f"{timestamp}:upload".encode(), hashlib.sha256).hexdigest()
        return {"X-Stem-Timestamp": timestamp, "X-Stem-Signature": signature}

    # Do not use OS proxy variables or follow a redirect to a non-local service.
    with httpx.Client(base_url=BASE_URL, timeout=90, trust_env=False, follow_redirects=False) as client:
        def checked(response: httpx.Response, expected: int = 200) -> httpx.Response:
            require(response.status_code == expected,
                    f"Local {response.request.method} {response.request.url.path} returned "
                    f"HTTP {response.status_code}, expected {expected}.")
            return response

        def wait_job(path: str, name: str) -> dict:
            deadline = min(total_deadline, time.monotonic() + CASE_TIMEOUT_SECONDS)
            while time.monotonic() < deadline:
                job = checked(client.get(local_path(path))).json()
                if job["status"] in {"failed", "completed"}:
                    require(job["status"] == "completed", f"{name} job {job['id']} failed.")
                    # JSON strictness catches accidental NumPy/NaN output issues.
                    json.dumps(job, allow_nan=False)
                    return job
                time.sleep(1)
            raise AssertionError(f"{name} job did not finish inside the bounded smoke timeout.")

        def audio_range(path: str) -> None:
            response = checked(client.get(local_path(path), headers={"Range": "bytes=0-1023"}), 206)
            require(response.headers.get("content-range", "").startswith("bytes 0-1023/"),
                    "Audio response did not honor the requested byte range.")
            require(len(response.content) == 1024, "Partial playback must contain exactly 1024 bytes.")
            require("no-store" in response.headers.get("cache-control", ""), "Audio must not be cached.")

        ready = checked(client.get("/ready")).json()
        require(ready["status"] == "ready" and all(ready["checks"].values()), "Processor is not ready.")
        health = checked(client.get("/health")).json()
        require(not any(health.get(key, 0) for key in (
            "active_jobs", "active_practice_jobs", "active_media_jobs", "active_youtube_imports",
        )), "Processor is busy with existing work. Wait before running the smoke test.")
        require(health.get("compute") == "local" and health.get("model_workers") == 1,
                "Expected the single-worker local processor.")
        checked(client.post("/practice/jobs"), 401)
        checked(client.options("/practice/jobs", headers={
            "Origin": "http://127.0.0.1:3007", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Stem-Timestamp,X-Stem-Signature,Content-Type",
        }))
        for origin in ("http://localhost:3007", "http://127.0.0.1:3007",
                       "https://stem-studio-murex.vercel.app"):
            response = checked(client.get("/ready", headers={"Origin": origin}))
            require(response.headers.get("access-control-allow-origin") == origin,
                    "Expected explicit allowed origin was missing.")
        denied = client.options("/practice/jobs", headers={
            "Origin": "https://untrusted.invalid", "Access-Control-Request-Method": "POST",
        })
        require(denied.status_code == 400 and "access-control-allow-origin" not in denied.headers,
                "Untrusted browser origin must not receive CORS permission.")
        report(stage="readiness_auth_cors", passed=True, cpu_threads=health["cpu_threads"])

        reference_audio = generated_vocal()
        short_audio = generated_vocal(duration=3)
        completed = {}
        cases = [
            ("reference", reference_audio, "isolated"),
            ("take", generated_vocal(delay=0.38, cents=-40), "isolated"),
            ("silence", generated_vocal(silence=True), "isolated"),
            ("mixed", short_audio, "mixed"),
        ]
        for name, content, kind in cases:
            begin = time.monotonic()
            submitted = checked(client.post("/practice/jobs", headers=headers(),
                files={"file": (f"generated-{name}.wav", content, "audio/wav")},
                data={"input_kind": kind, "duration_seconds": "10"}), 202).json()
            job = wait_job(f"/practice/jobs/{submitted['id']}", name)
            require(bool(job["result"]["frames"]), f"{name} must return a time axis.")
            audio_range(job["audio_url"])
            completed[name] = job
            report(stage="practice", case=name, id=job["id"],
                   seconds=round(time.monotonic() - begin, 2),
                   voiced_percent=job["result"]["summary"]["voiced_percent"], range_206=True)

        def compare(take_id: str, reference_id: str, alignment: str) -> dict:
            return checked(client.post("/practice/compare", headers=headers(), json={
                "take_job_id": take_id, "reference_job_id": reference_id, "alignment": alignment,
            })).json()

        reference = completed["reference"]
        identity = compare(reference["id"], reference["id"], "manual")
        require(identity["score"] == 100, "An identical phrase must score 100.")
        performed = compare(completed["take"]["id"], reference["id"], "auto")
        require(performed["reliable"], "Known shifted take must produce a reliable comparison.")
        offset = performed["alignment"]["offset_seconds"]
        bias = performed["metrics"]["bias_cents"]
        require(abs(offset + 0.38) < 0.12, "Known 380 ms delay was not recovered.")
        require(-60 < bias < -20, "Known 40-cent flat take was not recovered.")
        silent = compare(completed["silence"]["id"], reference["id"], "manual")
        require(silent["score"] is None, "Silent take must not receive a singing score.")
        report(stage="comparison", identity_score=identity["score"], detuned_score=performed["score"],
               offset_seconds=offset, bias_cents=bias, silence_is_unscored=True)

        trimmed = checked(client.post("/tools/trim-merge", headers=headers(),
            files={"file": ("generated-reference.wav", reference_audio, "audio/wav")},
            data={"segments": json.dumps([
                {"start_seconds": 0, "end_seconds": 1}, {"start_seconds": 5, "end_seconds": 999},
            ])}))
        trim_duration = decoded_duration(trimmed.content)
        require(abs(trim_duration - 2.8) < 0.2, "Trim must merge ordered parts and clamp the end to 6.8 seconds.")
        video = generated_video(short_audio)
        extracted = checked(client.post("/tools/extract-mp3", headers=headers(),
            files={"file": ("generated-video.mp4", video, "video/mp4")}))
        extraction_duration = decoded_duration(extracted.content)
        require(2.9 < extraction_duration < 3.3, "Generated video audio was not correctly extracted.")
        report(stage="edit_and_convert", trim_seconds=round(trim_duration, 3),
               extraction_seconds=round(extraction_duration, 3))

        begin = time.monotonic()
        submitted = checked(client.post("/jobs", headers=headers(),
            files={"file": ("generated-three-seconds.wav", short_audio, "audio/wav")},
            data={"mode": "stems"}), 202).json()
        stems = wait_job(f"/jobs/{submitted['id']}", "stems")
        stem_durations = {}
        for name in ("instrumental", "drums", "vocals"):
            audio_range(stems[f"{name}_url"])
            output = checked(client.get(local_path(stems[f"{name}_url"]))).content
            duration = decoded_duration(output)
            require(abs(duration - 3) < 0.15, f"{name} stem duration did not match the source.")
            stem_durations[name] = round(duration, 3)
            shared = checked(client.get(f"/jobs/{stems['id']}/share/{name}"))
            require(abs(decoded_duration(shared.content) - 3) < 0.2, f"{name} mobile MP3 was not generated.")
        report(stage="stems", id=stems["id"], seconds=round(time.monotonic() - begin, 2),
               durations=stem_durations, share_mp3=True)

        reused = checked(client.post("/practice/jobs", headers=headers(), data={
            "source_job_id": stems["id"], "input_kind": "mixed", "duration_seconds": "3",
        }), 202).json()
        reused = wait_job(f"/practice/jobs/{reused['id']}", "reused vocals")
        require(reused["input_kind"] == "isolated", "Existing vocals must skip a second separation.")
        audio_range(reused["audio_url"])
        report(stage="reuse_vocals", id=reused["id"], input_kind=reused["input_kind"])

        mixed = checked(client.post("/tools/mix", headers=headers(),
            files={"vocal": ("generated-vocal.wav", short_audio, "audio/wav")},
            data={"instrumental_job_id": stems["id"], "offset_ms": "125",
                  "trim_start_seconds": "0.25", "trim_end_seconds": "2.5",
                  "vocal_gain_db": "-3", "instrumental_gain_db": "-6"})).json()
        mix_durations = {}
        for output_format in ("wav", "mp3"):
            output = checked(client.get(local_path(mixed[f"{output_format}_url"]))).content
            duration = decoded_duration(output)
            require(abs(duration - 3) < 0.2, "Mixed export must retain the 3-second instrumental duration.")
            mix_durations[output_format] = round(duration, 3)
            audio_range(mixed[f"{output_format}_url"])
        report(stage="mix", id=mixed["id"], durations=mix_durations)

        return {"passed": True, "local_only": True, "seconds": round(time.monotonic() - started, 2),
                "practice_jobs": {name: job["id"] for name, job in completed.items()},
                "stems_job": stems["id"], "reused_vocals_job": reused["id"], "mix_job": mixed["id"],
                "identity_score": identity["score"], "offset_seconds": offset, "bias_cents": bias,
                "silence_is_unscored": True}


def main() -> int:
    try:
        report(**verify(load_secret()))
    except (AssertionError, RuntimeError, OSError, httpx.HTTPError, subprocess.SubprocessError) as exc:
        # No headers, credentials, raw response bodies, or recordings in output.
        report(passed=False, error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
