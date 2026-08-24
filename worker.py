from __future__ import annotations

import argparse
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from supabase import Client, create_client

from stem_studio.audio import run_demucs


def get_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    secret = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not secret:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_SECRET_KEY before starting the worker.")
    return create_client(url, secret)


def claim_next_job(client: Client) -> dict[str, Any] | None:
    queued = (
        client.table("separation_jobs")
        .select("*")
        .eq("status", "queued")
        .order("created_at")
        .limit(1)
        .execute()
    )
    if not queued.data:
        return None
    job = queued.data[0]
    claimed = (
        client.table("separation_jobs")
        .update({"status": "processing", "progress": 5, "error": None})
        .eq("id", job["id"])
        .eq("status", "queued")
        .select("id")
        .execute()
    )
    return job if claimed.data else None


def update_job(client: Client, job_id: str, **values: Any) -> None:
    client.table("separation_jobs").update(values).eq("id", job_id).execute()


def process_job(client: Client, job: dict[str, Any]) -> None:
    job_id = job["id"]
    user_id = job["user_id"]
    extension = Path(job["source_path"]).suffix or ".audio"
    try:
        with tempfile.TemporaryDirectory(prefix=f"stem-studio-{job_id[:8]}-") as temporary:
            workdir = Path(temporary)
            source = workdir / f"source{extension}"
            source.write_bytes(client.storage.from_("audio").download(job["source_path"]))
            update_job(client, job_id, progress=15)

            outputs = run_demucs(source, workdir / "separated")
            update_job(client, job_id, progress=85)

            remote_paths: dict[str, str] = {}
            for stem_name, local_path in outputs.items():
                remote_path = f"{user_id}/jobs/{job_id}/outputs/{stem_name}.wav"
                with Path(local_path).open("rb") as audio_file:
                    client.storage.from_("audio").upload(
                        path=remote_path,
                        file=audio_file,
                        file_options={"cache-control": "3600", "content-type": "audio/wav", "upsert": "false"},
                    )
                remote_paths[f"{stem_name}_path"] = remote_path

            update_job(client, job_id, status="completed", progress=100, **remote_paths)
            print(f"Completed {job['source_name']} ({job_id})", flush=True)
    except Exception as exc:
        update_job(client, job_id, status="failed", error=str(exc)[:1000])
        print(f"Failed {job_id}: {exc}", flush=True)


def run(once: bool = False, poll_seconds: float = 5) -> None:
    client = get_client()
    print("Stem Studio worker is ready.", flush=True)
    while True:
        job = claim_next_job(client)
        if job:
            process_job(client, job)
        elif once:
            return
        else:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process queued Stem Studio separation jobs.")
    parser.add_argument("--once", action="store_true", help="Check the queue once, then exit.")
    parser.add_argument("--poll-seconds", type=float, default=5)
    args = parser.parse_args()
    run(once=args.once, poll_seconds=args.poll_seconds)
