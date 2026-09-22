#!/bin/zsh
# Login-scoped, loopback-only UI. Never publish port 3007 through a tunnel.
set -euo pipefail
script_directory="${0:A:h}"
project_directory="${script_directory:h}"
action="${1:-install}"
python_path="${project_directory}/.venv/bin/python"
plist_path="${HOME}/Library/LaunchAgents/com.stemstudio.web.plist"
logs_directory="${HOME}/Library/Logs"
backup_directory="${HOME}/Library/Application Support/Stem Studio/service-backups"
service_domain="gui/$(id -u)"
service_target="${service_domain}/com.stemstudio.web"

usage() {
  print "Usage: zsh scripts/install_macos_web.sh {install|start|stop|restart|status|uninstall}"
  print "Private local UI: http://127.0.0.1:3007. Stop persists until start. Never tunnel this port."
}
if [[ "${action}" == "help" || "${action}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ "$(uname -s)" != "Darwin" || "$(id -u)" == "0" ]]; then
  print -u2 "Run as your regular macOS user, without sudo."
  exit 2
fi
stop_service() {
  launchctl disable "${service_target}"
  if launchctl print "${service_target}" >/dev/null 2>&1; then
    launchctl bootout "${service_target}"
  fi
}
start_service() {
  if [[ ! -f "${plist_path}" ]]; then
    print -u2 "Run install first."
    exit 2
  fi
  launchctl enable "${service_target}"
  if ! launchctl print "${service_target}" >/dev/null 2>&1; then
    launchctl bootstrap "${service_domain}" "${plist_path}"
  fi
  launchctl kickstart "${service_target}"
}
report_readiness() {
  for attempt in {1..10}; do
    if curl --fail --silent --max-time 1 --output /dev/null http://127.0.0.1:3007/; then
      print "Local interface is responding: http://127.0.0.1:3007"
      return
    fi
    sleep 1
  done
  print -u2 "Installed, but not responding yet. Inspect status/logs and any macOS Documents-access prompt; do not disable privacy protection."
}
case "${action}" in
  status)
    launchctl print "${service_target}" || print "Local interface is not running."
    print "Logs: ${logs_directory}/StemStudioWeb.log and StemStudioWeb.error.log"
    ;;
  stop)
    stop_service
    print "Local interface stopped; run start to re-enable."
    ;;
  start)
    start_service
    report_readiness
    ;;
  restart)
    stop_service
    start_service
    report_readiness
    ;;
  uninstall)
    stop_service
    if [[ -f "${plist_path}" ]]; then
      mkdir -p "${backup_directory}"
      backup_path="${backup_directory}/com.stemstudio.web.$(date +%Y%m%d-%H%M%S).$$.plist"
      mv "${plist_path}" "${backup_path}"
      print "Local interface removed; recoverable launch-agent backup: ${backup_path}"
    fi
    print "Project files, recordings, models, logs, processor, and Keychain were preserved."
    ;;
  install)
    if [[ ! -x "${python_path}" || ! -f "${project_directory}/web/.next-mac/BUILD_ID" ]]; then
      print -u2 "Build first: .venv/bin/python -m stem_studio.mac_web build"
      exit 2
    fi
    command -v node >/dev/null || { print -u2 "Install Node.js first."; exit 2; }
    cd "${project_directory}"
    "${python_path}" -m stem_studio.mac_service --check
    umask 077
    mkdir -p "${plist_path:h}" "${logs_directory}"
    temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/stem-studio-web.XXXXXXXX")"
    temporary_plist="${temporary_directory}/com.stemstudio.web.plist"
    trap 'rm -f "${temporary_plist}"; rmdir "${temporary_directory}"' EXIT
    install -m 600 "${script_directory}/com.stemstudio.web.plist" "${temporary_plist}"
    plutil -remove ProgramArguments.0 "${temporary_plist}"
    plutil -insert ProgramArguments.0 -string "${python_path}" "${temporary_plist}"
    plutil -replace WorkingDirectory -string "${project_directory}" "${temporary_plist}"
    plutil -replace StandardOutPath -string "${logs_directory}/StemStudioWeb.log" "${temporary_plist}"
    plutil -replace StandardErrorPath -string "${logs_directory}/StemStudioWeb.error.log" "${temporary_plist}"
    plutil -lint "${temporary_plist}" >/dev/null
    if [[ -f "${plist_path}" ]]; then
      mkdir -p "${backup_directory}"
      cp -p "${plist_path}" "${backup_directory}/com.stemstudio.web.$(date +%Y%m%d-%H%M%S).$$.plist"
    fi
    if launchctl print "${service_target}" >/dev/null 2>&1; then
      launchctl bootout "${service_target}"
    fi
    install -m 600 "${temporary_plist}" "${plist_path}"
    start_service
    print "Local Stem Studio interface installed for login startup."
    report_readiness
    ;;
  *) usage >&2; exit 2 ;;
esac
