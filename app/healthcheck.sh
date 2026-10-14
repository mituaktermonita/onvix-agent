#!/bin/bash
set -euo pipefail
check() {
  [ -f /run/ctl/supervisor-alive ] || return 1
  [ "$(( $(date +%s) - $(stat -c %Y /run/ctl/supervisor-alive) ))" -lt 90 ] || return 1
  curl -fsS --max-time 3 http://127.0.0.1:20128/api/monitoring/health >/dev/null || return 1
  curl -fsS --max-time 3 http://127.0.0.1:8080/healthz >/dev/null || return 1
  if [ -f /home/launcher/state/started ]; then
    curl -fsS --max-time 3 http://127.0.0.1:18789/readyz >/dev/null || return 1
  fi
}
if [ "${1:-}" = --wait ]; then
  deadline=$((SECONDS + ${2:-180}))
  until check; do
    if (( SECONDS >= deadline )); then
      echo 'Readiness timed out. Inspect /var/log/agent/{omniroute,openclaw,render}.log and /var/log/supervisor.out' >&2
      exit 1
    fi
    sleep 2
  done
else
  check
fi
