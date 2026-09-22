"""Build/run the private loopback web UI, without storing a secret in a file.

Never tunnel this UI: it issues upload signatures without the Vercel password
because only programs on this Mac can reach it. Only the authenticated processor
may be forwarded by the separately authorized remote-access service.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from stem_studio.mac_service import KeychainError, load_secret

HOST = "127.0.0.1"
PORT = 3007
PROCESSOR_URL = "http://127.0.0.1:8766"


def runtime_environment(secret: str | None = None) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update({
        "NODE_ENV": "production",
        "NEXT_TELEMETRY_DISABLED": "1",
        "STEM_STUDIO_LOCAL_WEB": "1",
        "STEM_STUDIO_PROCESSOR_LOCATION": "mac",
        "NEXT_PUBLIC_PROCESSOR_URL": PROCESSOR_URL,
        # Empty values override any dotenv files; never inherit hosted settings.
        "STEM_STUDIO_PASSWORD": "",
        "PROCESSOR_SHARED_SECRET": secret or "",
    })
    return environment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Private Stem Studio interface on this Mac.")
    parser.add_argument("action", choices=("build", "start"), nargs="?", default="start")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        print("This entry point requires macOS.", file=sys.stderr)
        return 1
    project_root = Path(__file__).resolve().parent.parent
    web_root = project_root / "web"
    next_cli = web_root / "node_modules" / "next" / "dist" / "bin" / "next"
    node = shutil.which("node")
    if not node or not next_cli.is_file():
        print("Install Node.js and run npm ci in web/ first.", file=sys.stderr)
        return 1
    if args.action == "build":
        # A build never needs the signing key; only the dynamic token route does.
        return subprocess.run(
            [node, str(next_cli), "build"], cwd=web_root, env=runtime_environment(), check=False,
        ).returncode
    if not (web_root / ".next-mac" / "BUILD_ID").is_file():
        print("Build first: .venv/bin/python -m stem_studio.mac_web build", file=sys.stderr)
        return 1
    try:
        environment = runtime_environment(load_secret())
    except KeychainError as exc:
        print(f"Stem Studio interface could not start: {exc}", file=sys.stderr)
        return 1
    os.umask(0o077)
    os.chdir(web_root)
    # exec preserves launchd's process-group lifecycle and keeps the key off argv.
    os.execve(node, [node, str(next_cli), "start", "--hostname", HOST, "--port", str(PORT)], environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
