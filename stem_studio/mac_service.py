"""Private macOS launchd entry point for the local audio processor.

The launch agent contains no credentials. Read the shared secret from the
current user's login Keychain at startup, before importing the API or any
numeric libraries. A missing/locked Keychain fails closed; it never silently
starts an unauthenticated server.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import pwd
import subprocess
import sys

KEYCHAIN_SERVICE = "Stem Studio Processor"
HOST = "127.0.0.1"
PORT = 8766
ALLOWED_ORIGINS = (
    "https://stem-studio-murex.vercel.app",
    "http://localhost:3007",
    "http://127.0.0.1:3007",
)
THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


class KeychainError(RuntimeError):
    """Actionable but credential-free startup failure."""


def read_keychain_secret(account: str | None = None) -> str:
    """Retrieve only this service's password without exposing it in arguments."""
    if account is None:
        account = pwd.getpwuid(os.getuid()).pw_name
    try:
        result = subprocess.run(
            [
                "/usr/bin/security", "find-generic-password",
                "-a", account, "-s", KEYCHAIN_SERVICE, "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        # In particular, never include CalledProcessError/TimeoutExpired output:
        # their captured stdout could contain the password.
        raise KeychainError(
            "Cannot read Stem Studio Processor from Keychain. Unlock the login "
            "Keychain and restart the processor."
        ) from None
    if result.returncode != 0:
        raise KeychainError(
            "Stem Studio Processor password is unavailable. Create the matching "
            "shared secret in this user's login Keychain, or unlock it, then restart."
        )
    secret = result.stdout.removesuffix("\n")
    if len(secret) < 32 or "\n" in secret or "\r" in secret or "\0" in secret:
        raise KeychainError("Stem Studio Processor password must contain at least 32 characters on one line.")
    return secret


load_secret = read_keychain_secret


def runtime_environment(secret: str, project_root: Path, user_home: Path) -> dict[str, str]:
    """Build the fixed service settings; inherited unsafe overrides are ignored."""
    data_root = user_home / "Library" / "Application Support" / "Stem Studio" / "processor"
    model_root = project_root / ".model-cache"
    return {
        "PROCESSOR_SHARED_SECRET": secret,
        "PROCESSOR_REQUIRE_AUTH": "1",
        "STEM_STUDIO_DATA_DIR": str(data_root),
        "STEM_STUDIO_MODEL_DIR": str(model_root),
        "TORCH_HOME": str(model_root),
        "STEM_STUDIO_CPU_THREADS": "4",
        "ALLOWED_ORIGINS": ",".join(ALLOWED_ORIGINS),
        "MAX_UPLOAD_MB": "150",
        "MAX_PENDING_JOBS": "3",
        "MAX_PENDING_IMPORTS": "1",
        "JOB_TTL_SECONDS": "3600",
        "YOUTUBE_IMPORT_TIMEOUT_SECONDS": "900",
        "PYTHONUNBUFFERED": "1",
        **dict.fromkeys(THREAD_VARIABLES, "4"),
    }


def configure_runtime() -> None:
    """Configure before importing processor.api, whose settings load at import."""
    project_root = Path(__file__).resolve().parent.parent
    environment = runtime_environment(read_keychain_secret(), project_root, Path.home())
    # Temp recordings are private even if the interactive shell's umask is lax.
    os.umask(0o077)
    for name in ("STEM_STUDIO_DATA_DIR", "STEM_STUDIO_MODEL_DIR"):
        directory = Path(environment[name])
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # mkdir's mode does not repair an older directory created by a local
        # development run with a permissive umask.
        directory.chmod(0o700)
    os.environ.update(environment)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the authenticated Stem Studio macOS processor.")
    parser.add_argument("--check", action="store_true", help="Check Keychain and paths without starting a server.")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        print("This entry point requires macOS. Use processor.api for other platforms.", file=sys.stderr)
        return 1
    try:
        configure_runtime()
    except (KeychainError, OSError) as exc:
        print(f"Stem Studio processor could not start: {exc}", file=sys.stderr)
        return 1
    if args.check:
        print("Stem Studio processor: Keychain and private storage are ready.")
        return 0

    import uvicorn

    # A Funnel is deliberately public. Never trust forwarded client addresses to
    # bypass auth, and do not log signed download URLs/session tokens in queries.
    uvicorn.run(
        "processor.api:app",
        host=HOST,
        port=PORT,
        workers=1,
        proxy_headers=False,
        access_log=False,
        server_header=False,
        timeout_graceful_shutdown=15,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
