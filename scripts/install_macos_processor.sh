#!/bin/zsh
# Manage this user's processor only. Does not install Python dependencies, create
# credentials, expose a public tunnel, or change power/network/system settings.
set -euo pipefail

script_directory="${0:A:h}"
project_directory="${script_directory:h}"
action="${1:-install}"
python_path="${project_directory}/.venv/bin/python"
template_path="${script_directory}/com.stemstudio.processor.plist"
launch_agents_directory="${HOME}/Library/LaunchAgents"
logs_directory="${HOME}/Library/Logs"
backup_directory="${HOME}/Library/Application Support/Stem Studio/service-backups"
plist_path="${launch_agents_directory}/com.stemstudio.processor.plist"
service_label="com.stemstudio.processor"
service_domain="gui/$(id -u)"
service_target="${service_domain}/${service_label}"

usage() {
  print "Usage: zsh scripts/install_macos_processor.sh {install|start|stop|restart|status|uninstall}"
  print "Install runs at user login and after service crashes. The Mac must remain awake."
  print "Stop remains stopped across logins until start. Uninstall preserves all audio, models and Keychain entries."
}

if [[ "${action}" == "help" || "${action}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ "$(uname -s)" != "Darwin" || "$(id -u)" == "0" ]]; then
  print -u2 "Run this as your regular macOS user, without sudo."
  exit 2
fi

stop_service() {
  launchctl disable "${service_target}"
  if launchctl print "${service_target}" >/dev/null 2>&1; then
    launchctl bootout "${service_target}"
  fi
}

processor_endpoint_reports() {
  local endpoint_state
  endpoint_state="$(/usr/bin/curl --noproxy '*' --fail --silent --connect-timeout 1 --max-time 2 \
    "http://127.0.0.1:8766/${1}" | /usr/bin/plutil -extract status raw -o - - 2>/dev/null)" || return 1
  [[ "${endpoint_state}" == "${2}" ]]
}

report_readiness() {
  if processor_endpoint_reports ready ready; then
    print "Processor is ready at http://127.0.0.1:8766."
  elif processor_endpoint_reports health ok; then
    print -u2 "Warning: the processor responds, but its readiness checks are not passing yet."
    print -u2 "Inspect http://127.0.0.1:8766/ready for missing dependencies, storage, or authentication settings."
  else
    print -u2 "Warning: the processor is not responding on http://127.0.0.1:8766. It is not ready to process audio."
    print -u2 "Check ${logs_directory}/StemStudioProcessor.error.log and run this script with status."
    print -u2 "A foreground diagnostic is: .venv/bin/python -m stem_studio.mac_service"
  fi
  return 0
}

wait_for_readiness() {
  local startup_deadline=$((SECONDS + 30))
  print "Waiting up to 30 seconds for the processor's readiness checks..."
  while (( SECONDS < startup_deadline )); do
    if processor_endpoint_reports ready ready; then
      print "Processor is ready at http://127.0.0.1:8766."
      return 0
    fi
    sleep 1
  done
  report_readiness
  print -u2 "The launch agent remains installed. A start request does not guarantee that the server became ready."
  return 0
}

start_service() {
  if [[ ! -f "${plist_path}" ]]; then
    print -u2 "Processor is not installed. Run this script with install first."
    exit 2
  fi
  launchctl enable "${service_target}"
  if ! launchctl print "${service_target}" >/dev/null 2>&1; then
    launchctl bootstrap "${service_domain}" "${plist_path}"
  fi
  launchctl kickstart "${service_target}"
  wait_for_readiness
}

case "${action}" in
  status)
    if ! launchctl print "${service_target}"; then
      print "Processor is not running."
    fi
    report_readiness
    print "Logs: ${logs_directory}/StemStudioProcessor.log and StemStudioProcessor.error.log"
    ;;
  stop)
    stop_service
    print "Processor stopped. Run this script with start to re-enable it."
    ;;
  start)
    start_service
    print "Processor start requested. Check status and http://127.0.0.1:8766/health."
    ;;
  restart)
    stop_service
    start_service
    print "Processor restart requested. In-flight jobs may be interrupted."
    ;;
  uninstall)
    stop_service
    if [[ -f "${plist_path}" ]]; then
      mkdir -p "${backup_directory}"
      backup_path="${backup_directory}/com.stemstudio.processor.$(date +%Y%m%d-%H%M%S).$$.plist"
      mv "${plist_path}" "${backup_path}"
      print "Processor launch agent removed; recoverable backup: ${backup_path}"
    fi
    print "Audio, model cache, logs, and Keychain entry were preserved. Any separately configured tunnel is unchanged."
    ;;
  install)
    if [[ ! -x "${python_path}" ]]; then
      print -u2 "Create this project's .venv and install requirements-processor.txt first."
      exit 2
    fi
    if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
      print -u2 "Install ffmpeg first: brew install ffmpeg"
      exit 2
    fi
    "${python_path}" -c 'import fastapi, uvicorn, multipart, numpy, soundfile, torch, torchcrepe, demucs' || {
      print -u2 "Missing dependencies. Run .venv/bin/python -m pip install -r requirements-processor.txt"
      exit 2
    }
    cd "${project_directory}"
    "${python_path}" -m stem_studio.mac_service --check
    umask 077
    mkdir -p "${launch_agents_directory}" "${logs_directory}"
    temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/stem-studio-processor.XXXXXXXX")"
    temporary_plist="${temporary_directory}/com.stemstudio.processor.plist"
    trap 'rm -f "${temporary_plist}"; rmdir "${temporary_directory}"' EXIT
    install -m 600 "${template_path}" "${temporary_plist}"
    # plutil -replace can insert (not replace) an array item on macOS.
    plutil -remove ProgramArguments.0 "${temporary_plist}"
    plutil -insert ProgramArguments.0 -string "${python_path}" "${temporary_plist}"
    plutil -replace WorkingDirectory -string "${project_directory}" "${temporary_plist}"
    plutil -replace StandardOutPath -string "${logs_directory}/StemStudioProcessor.log" "${temporary_plist}"
    plutil -replace StandardErrorPath -string "${logs_directory}/StemStudioProcessor.error.log" "${temporary_plist}"
    plutil -lint "${temporary_plist}" >/dev/null
    if [[ -f "${plist_path}" ]]; then
      mkdir -p "${backup_directory}"
      cp -p "${plist_path}" "${backup_directory}/com.stemstudio.processor.$(date +%Y%m%d-%H%M%S).$$.plist"
    fi
    if launchctl print "${service_target}" >/dev/null 2>&1; then
      launchctl bootout "${service_target}"
    fi
    install -m 600 "${temporary_plist}" "${plist_path}"
    start_service
    print "Stem Studio processor installed for this user's login and start requested."
    print "Configured private endpoint: http://127.0.0.1:8766 (a separately configured authenticated HTTPS tunnel is needed remotely)."
    print "Check: zsh scripts/install_macos_processor.sh status"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
