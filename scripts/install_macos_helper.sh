#!/bin/zsh
set -euo pipefail

script_directory="${0:A:h}"
project_directory="${script_directory:h}"
broker_url="${1:-${STEM_STUDIO_WORKER_URL:-}}"
worker_secret="${STEM_STUDIO_WORKER_SECRET:-}"
python_path="${project_directory}/.venv/bin/python"
template_path="${script_directory}/com.stemstudio.youtube-helper.plist"
launch_agents_directory="${HOME}/Library/LaunchAgents"
logs_directory="${HOME}/Library/Logs"
plist_path="${launch_agents_directory}/com.stemstudio.youtube-helper.plist"
service_target="gui/$(id -u)/com.stemstudio.youtube-helper"

if [[ -z "${broker_url}" ]]; then
  print -u2 "Usage: STEM_STUDIO_WORKER_SECRET=... $0 https://YOUR-HELPER-BROKER.modal.run"
  exit 2
fi
if [[ "${broker_url}" != https://* ]]; then
  print -u2 "The private helper broker URL must use HTTPS."
  exit 2
fi
if [[ -z "${worker_secret}" ]]; then
  read -r -s "worker_secret?Private helper secret: "
  print
fi
if [[ -z "${worker_secret}" ]]; then
  print -u2 "The private helper secret cannot be empty."
  exit 2
fi
if [[ ! -x "${python_path}" ]]; then
  print -u2 "Create the project .venv first; ${python_path} was not found."
  exit 2
fi
if ! command -v ffmpeg >/dev/null || ! command -v deno >/dev/null; then
  print -u2 "Install ffmpeg and deno first: brew install ffmpeg deno"
  exit 2
fi

"${python_path}" -c 'import httpx, yt_dlp' 2>/dev/null || \
  "${python_path}" -m pip install -r "${project_directory}/requirements-worker.txt"

security add-generic-password \
  -a "${USER}" \
  -s "Stem Studio Home Worker" \
  -w "${worker_secret}" \
  -U >/dev/null

mkdir -p "${launch_agents_directory}" "${logs_directory}"
install -m 600 "${template_path}" "${plist_path}"
plutil -replace ProgramArguments.0 -string "${python_path}" "${plist_path}"
plutil -replace ProgramArguments.4 -string "${broker_url}" "${plist_path}"
plutil -replace WorkingDirectory -string "${project_directory}" "${plist_path}"
plutil -replace StandardOutPath -string "${logs_directory}/StemStudioHelper.log" "${plist_path}"
plutil -replace StandardErrorPath -string "${logs_directory}/StemStudioHelper.error.log" "${plist_path}"
plutil -lint "${plist_path}" >/dev/null

launchctl bootout "${service_target}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "${plist_path}"
launchctl kickstart -k "${service_target}"

print "Stem Studio helper installed and started."
print "Log: ${logs_directory}/StemStudioHelper.log"
