"""Opt-in production smoke test using generated audio and existing upload auth.

Run: modal run scripts/verify_practice_live.py
This creates four temporary analysis jobs (one includes Demucs). It spends a
small amount of Modal compute; it never exports the upload signing secret.
"""
import array
import hashlib
import hmac
import io
import json
import math
import os
import time
import wave

import modal

app = modal.App("stem-studio-practice-verification")
image = modal.Image.debian_slim(python_version="3.11").pip_install("httpx>=0.28,<1")
DEFAULT_URL = "https://naman19india--stem-studio-processor-web.modal.run"


def generated_vocal(*, delay: float = 0, cents: float = 0, silence: bool = False) -> bytes:
    rate = 16000
    pitches = [57, 61, 59, 64, 62, 66, 64, 69]
    samples = array.array("h")
    phase = 0.0
    for index in range(round((6.8 + delay) * rate)):
        moment = index / rate - delay - 0.2
        note = math.floor(moment / 0.8)
        local = moment - note * 0.8
        voiced = not silence and 0 <= note < len(pitches) and local < 0.7
        if voiced:
            hz = 440 * 2 ** ((pitches[note] - 69 + cents / 100) / 12)
            phase += 2 * math.pi * hz / rate
            envelope = min(1.0, local / 0.025, (0.7 - local) / 0.025)
            value = envelope * (0.20 * math.sin(phase) + 0.32 * math.sin(2 * phase)
                                + 0.16 * math.sin(3 * phase) + 0.08 * math.sin(4 * phase))
        else:
            value = 0.0
        samples.append(round(max(-0.95, min(0.95, value)) * 32767))
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(samples.tobytes())
    return output.getvalue()


@app.function(image=image, secrets=[modal.Secret.from_name("stem-studio-upload-secret")], timeout=900)
def verify(base_url: str = DEFAULT_URL, include_mixed: bool = True) -> dict:
    import httpx

    # Keep this verification credential scoped to our deployed processor.
    if base_url.rstrip("/") != DEFAULT_URL:
        raise ValueError("This authenticated smoke test is limited to the Stem Studio processor.")
    secret = os.environ["PROCESSOR_SHARED_SECRET"]

    def headers() -> dict[str, str]:
        timestamp = str(int(time.time()))
        signature = hmac.new(secret.encode(), f"{timestamp}:upload".encode(), hashlib.sha256).hexdigest()
        return {"X-Stem-Timestamp": timestamp, "X-Stem-Signature": signature}

    completed = {}
    with httpx.Client(base_url=base_url, timeout=90) as client:
        unauthorized = client.post("/practice/jobs")
        assert unauthorized.status_code == 401, "Practice upload must require authentication"
        cases = [
            ("reference", generated_vocal(), "isolated"),
            ("take", generated_vocal(delay=0.38, cents=-40), "isolated"),
            ("silence", generated_vocal(silence=True), "isolated"),
        ]
        if include_mixed:
            cases.append(("mixed", generated_vocal(), "mixed"))
        for name, audio, kind in cases:
            response = client.post("/practice/jobs", headers=headers(),
                                   files={"file": (f"verification-{name}.wav", audio, "audio/wav")},
                                   data={"input_kind": kind, "duration_seconds": "10"})
            response.raise_for_status()
            job_id = response.json()["id"]
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                response = client.get(f"/practice/jobs/{job_id}")
                response.raise_for_status()
                job = response.json()
                if job["status"] in {"failed", "completed"}:
                    break
                time.sleep(3)
            else:
                raise AssertionError(f"{name} analysis did not finish within five minutes")
            assert job["status"] == "completed", f"{name} failed: {job.get('error')}"
            assert job["result"]["frames"], f"{name} must have a time axis"
            json.dumps(job, allow_nan=False)
            playback = client.get(job["audio_url"], headers={"Range": "bytes=0-1023"})
            assert playback.status_code in {200, 206}
            assert "no-store" in playback.headers.get("cache-control", "")
            assert playback.content
            completed[name] = job

        def compare(take_id: str, reference_id: str, alignment: str) -> dict:
            response = client.post("/practice/compare", headers=headers(), json={
                "take_job_id": take_id, "reference_job_id": reference_id, "alignment": alignment,
            })
            response.raise_for_status()
            return response.json()

        reference = completed["reference"]
        identity = compare(reference["id"], reference["id"], "manual")
        assert identity["score"] == 100, identity
        performed = compare(completed["take"]["id"], reference["id"], "auto")
        assert performed["reliable"], performed["unscored_reason"]
        assert abs(performed["alignment"]["offset_seconds"] + 0.38) < 0.12, performed["alignment"]
        assert -60 < performed["metrics"]["bias_cents"] < -20, performed["metrics"]
        silent = compare(completed["silence"]["id"], reference["id"], "manual")
        assert silent["score"] is None
        return {
            "passed": True,
            "jobs": {name: {"id": value["id"], "voiced_percent": value["result"]["summary"]["voiced_percent"]}
                     for name, value in completed.items()},
            "identity_score": identity["score"],
            "detuned_take": {"score": performed["score"], "alignment": performed["alignment"], "metrics": performed["metrics"]},
            "silence_is_unscored": silent["score"] is None,
        }


@app.local_entrypoint()
def main(include_mixed: bool = True):
    print(json.dumps(verify.remote(include_mixed=include_mixed), indent=2))
