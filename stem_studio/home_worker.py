from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import platform
import random
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from stem_studio.youtube import YouTubeImportError, import_youtube_audio

KEYCHAIN_SERVICE = "Stem Studio Home Worker"
DEFAULT_POLL_SECONDS = 180
HEARTBEAT_SECONDS = 45


class HelperClientError(RuntimeError):
    """A safe helper-to-broker communication error."""


class HelperAuthenticationError(HelperClientError):
    """The installed helper secret no longer matches the broker."""


def _validated_broker_url(raw_url: str) -> str:
    value = raw_url.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise HelperClientError("The private helper URL is invalid.") from exc
    local = parsed.hostname in {"127.0.0.1", "localhost"}
    if parsed.scheme not in ({"http", "https"} if local else {"https"}):
        raise HelperClientError("The private helper URL must use HTTPS.")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HelperClientError("The private helper URL is invalid.")
    if port is not None and not local:
        raise HelperClientError("The private helper URL cannot use a custom port.")
    return value


def _load_worker_secret() -> str:
    configured = os.environ.get("STEM_STUDIO_WORKER_SECRET", "").strip()
    if configured:
        return configured
    if sys.platform == "darwin":
        account = os.environ.get("USER") or getpass.getuser()
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    if sys.stdin.isatty():
        return getpass.getpass("Stem Studio private helper secret: ").strip()
    raise HelperClientError(
        "No private helper credential was found. Set STEM_STUDIO_WORKER_SECRET or install it in Keychain."
    )


def _worker_id() -> str:
    machine = f"{platform.node()}:{platform.system()}:{platform.machine()}"
    return f"home-{hashlib.sha256(machine.encode()).hexdigest()[:12]}"


class HelperClient:
    def __init__(self, broker_url: str, secret: str) -> None:
        self.broker_url = _validated_broker_url(broker_url)
        self.client = httpx.Client(
            base_url=self.broker_url,
            headers={"Authorization": f"Bearer {secret}"},
            follow_redirects=False,
            timeout=httpx.Timeout(30, read=600, write=600),
        )

    def close(self) -> None:
        self.client.close()

    def claim(self) -> dict[str, object] | None:
        response = self.client.post("/worker/youtube/claim", data={"worker_id": _worker_id()})
        if response.status_code == 204:
            return None
        self._raise_for_status(response, "Could not check the private helper queue.")
        payload = response.json()
        if not isinstance(payload, dict) or not all(payload.get(key) for key in ("id", "url", "lease_token")):
            raise HelperClientError("The private helper broker returned an invalid job.")
        return payload

    def heartbeat(self, job_id: str, lease_token: str) -> None:
        response = self.client.post(
            f"/worker/youtube/{job_id}/heartbeat",
            headers={"X-Worker-Lease": lease_token},
        )
        self._raise_for_status(response, "The private helper lease could not be renewed.")

    def complete(self, job_id: str, lease_token: str, audio: Path, title: str) -> None:
        digest = hashlib.sha256()
        with audio.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        with audio.open("rb") as source:
            response = self.client.post(
                f"/worker/youtube/{job_id}/complete",
                headers={"X-Worker-Lease": lease_token},
                data={"title": title, "sha256": digest.hexdigest()},
                files={"file": ("source.mp3", source, "audio/mpeg")},
            )
        self._raise_for_status(response, "The prepared MP3 could not be returned to Stem Studio.")

    def fail(self, job_id: str, lease_token: str, code: str) -> None:
        response = self.client.post(
            f"/worker/youtube/{job_id}/fail",
            headers={"X-Worker-Lease": lease_token},
            data={"code": code},
        )
        self._raise_for_status(response, "The helper could not update the failed job.")

    @staticmethod
    def _raise_for_status(response: httpx.Response, fallback: str) -> None:
        if response.is_success:
            return
        if response.status_code in {401, 403}:
            raise HelperAuthenticationError("The private helper credential was rejected.")
        if response.status_code in {404, 409}:
            raise HelperClientError("This helper job is no longer available.")
        raise HelperClientError(fallback)


def _heartbeat_loop(
    client: HelperClient,
    job_id: str,
    lease_token: str,
    stop: threading.Event,
    lease_lost: threading.Event,
) -> None:
    failures = 0
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            client.heartbeat(job_id, lease_token)
            failures = 0
        except (HelperClientError, httpx.HTTPError):
            failures += 1
            if failures >= 3:
                lease_lost.set()
                return


def process_one_claim(client: HelperClient, claim: dict[str, object]) -> None:
    job_id = str(claim["id"])
    lease_token = str(claim["lease_token"])
    canonical_url = str(claim["url"])
    stop_heartbeat = threading.Event()
    lease_lost = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(client, job_id, lease_token, stop_heartbeat, lease_lost),
        daemon=True,
        name="stem-studio-helper-heartbeat",
    )
    heartbeat.start()
    print(f"Stem Studio helper claimed job {job_id[:8]}.", flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix="stem-studio-home-helper-") as temporary:
            imported = import_youtube_audio(canonical_url, Path(temporary))
            if lease_lost.is_set():
                raise HelperClientError("The helper lease was lost before the MP3 was ready.")
            client.complete(job_id, lease_token, imported.path, imported.title)
        print(f"Stem Studio helper completed job {job_id[:8]}.", flush=True)
    except YouTubeImportError as exc:
        if not lease_lost.is_set():
            try:
                client.fail(job_id, lease_token, exc.code)
            except (HelperClientError, httpx.HTTPError):
                pass
        print(f"Stem Studio helper could not complete job {job_id[:8]} ({exc.code}).", flush=True)
    finally:
        stop_heartbeat.set()
        heartbeat.join(timeout=2)


def run_forever(client: HelperClient, poll_seconds: int, *, once: bool = False) -> int:
    print("Stem Studio private helper is ready.", flush=True)
    while True:
        try:
            claim = client.claim()
            if claim is not None:
                process_one_claim(client, claim)
                if once:
                    return 0
            elif once:
                print("No Stem Studio helper job is waiting.", flush=True)
                return 0
        except HelperAuthenticationError as exc:
            print(f"Stem Studio helper authentication notice: {exc}", flush=True)
            if once:
                return 2
            time.sleep(15 * 60)
            continue
        except (HelperClientError, httpx.HTTPError) as exc:
            print(f"Stem Studio helper connection notice: {exc}", flush=True)
            if once:
                return 1
        try:
            time.sleep(max(10, poll_seconds) + random.uniform(0, 5))
        except KeyboardInterrupt:
            return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Private outbound helper for Stem Studio YouTube imports.")
    parser.add_argument(
        "--broker-url",
        default=os.environ.get("STEM_STUDIO_WORKER_URL", ""),
        help="The private Modal helper-broker URL.",
    )
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--once", action="store_true", help="Check once, process at most one job, then exit.")
    args = parser.parse_args()
    if not args.broker_url:
        parser.error("Provide --broker-url or STEM_STUDIO_WORKER_URL.")
    try:
        secret = _load_worker_secret()
        client = HelperClient(args.broker_url, secret)
    except HelperClientError as exc:
        print(f"Stem Studio helper setup error: {exc}", file=sys.stderr)
        return 2
    try:
        return run_forever(client, args.poll_seconds, once=args.once)
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
