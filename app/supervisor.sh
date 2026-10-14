#!/bin/bash
set -Eeuo pipefail
umask 077
APP=/opt/app
CTL=/run/ctl
LOG=/var/log/agent
VOL=/vol
export PATH="/opt/runtime/node_modules/.bin:$PATH"
exec 9>/run/supervisor.lock

if [ "${1:-}" = --stop ]; then
  if flock -n 9; then exit 0; fi
  touch /run/onvix.stop
  flock -w 480 9 || { echo 'Supervisor shutdown/backup timed out; inspect logs' >&2; exit 1; }
  [ "$(cat /run/onvix-stop-result 2>/dev/null || echo 1)" = 0 ] || { echo 'Final backup failed; preserve this sandbox' >&2; exit 1; }
  exit 0
fi
flock -n 9 || { echo 'Supervisor already running'; exit 0; }
[ "$(id -u)" = 0 ] || { echo 'Supervisor requires root'; exit 1; }
[ -s /run/agent.env ] || { echo 'Missing /run/agent.env'; exit 1; }
set -a
# shellcheck source=/dev/null
. /run/agent.env
set +a
for key in OMNIROUTE_PASSWORD JWT_SECRET API_KEY_SECRET STORAGE_ENCRYPTION_KEY LAUNCHER_TOKEN OPENCLAW_GATEWAY_TOKEN; do
  [ -n "${!key:-}" ] || { echo "Missing $key" >&2; exit 1; }
done
node "$APP/verify-runtime.mjs"
mountpoint -q "$VOL" || { echo '/vol is not mounted; refusing ephemeral-only state'; exit 1; }
python3 "$APP/backup.py" verify-key
mkdir -p "$CTL" "$LOG" /var/lib/onvix
chown launcher:launcher "$CTL"
chmod 750 "$CTL"
rm -f /run/onvix.stop /run/onvix-stop-result "$CTL/cmd.processing"
printf '%s\n' "$$" > /run/onvix.pid
restored=0
backup_pid=''
status() { printf '%s\n' "$1" > "$CTL/.status.tmp"; chmod 644 "$CTL/.status.tmp"; mv "$CTL/.status.tmp" "$CTL/status"; }
stop_user() {
  local user="$1"
  pkill -TERM -u "$user" 2>/dev/null || true
  for _ in $(seq 40); do pgrep -u "$user" >/dev/null || return 0; sleep .5; done
  pkill -KILL -u "$user" 2>/dev/null || true
}
backup() { timeout 180 python3 "$APP/backup.py" backup >>"$LOG/backup.log" 2>&1; }
cleanup() {
  local code=$?
  trap - EXIT INT TERM
  set +e
  status stopping
  stop_user openclaw
  stop_user launcher
  stop_user omniroute
  if [ -n "$backup_pid" ]; then wait "$backup_pid"; fi
  if [ "$restored" = 1 ]; then
    if ! backup; then echo 'Final backup FAILED; do not delete this sandbox' >&2; code=1; fi
  fi
  status stopped
  rm -f /run/onvix.pid "$CTL/supervisor-alive" /run/onvix.stop
  printf '%s\n' "$code" > /run/onvix-stop-result
  exit "$code"
}
trap cleanup EXIT
trap 'exit 0' INT TERM

if [ ! -f /var/lib/onvix/restored ]; then
  python3 "$APP/backup.py" restore
  touch /var/lib/onvix/restored
fi
for pair in omniroute:data openclaw:.openclaw launcher:state; do
  user=${pair%%:*}; dir="/home/$user/${pair#*:}"
  mkdir -p "$dir"
  chown -R "$user:$user" "$dir"
  chmod 700 "$dir"
done
restored=1

as() {
  local user="$1"; shift
  # Children must NOT inherit the supervisor's flock descriptor or root secrets.
  (cd "/home/$user" && exec runuser -u "$user" -- env -i PATH="$PATH" HOME="/home/$user" "$@" 9>&-)
}
omni_start() {
  as omniroute DATA_DIR=/home/omniroute/data PORT=20128 NODE_ENV=production \
    INITIAL_PASSWORD="$OMNIROUTE_PASSWORD" JWT_SECRET="$JWT_SECRET" API_KEY_SECRET="$API_KEY_SECRET" \
    STORAGE_ENCRYPTION_KEY="$STORAGE_ENCRYPTION_KEY" MACHINE_ID_SALT="$API_KEY_SECRET" \
    OMNIROUTE_WS_BRIDGE_SECRET="$JWT_SECRET" AUTH_COOKIE_SECURE=true REQUIRE_API_KEY=true \
    BASE_URL=http://127.0.0.1:20128 NEXT_PUBLIC_BASE_URL="$DASHBOARD_URL" \
    bash "$APP/start-omniroute.sh" >>"$LOG/omniroute.log" 2>&1 &
  omni_pid=$!
}
launcher_start() {
  as launcher STATE_DIR=/home/launcher/state CTL_DIR="$CTL" LAUNCHER_TOKEN="$LAUNCHER_TOKEN" \
    CONTROL_BOT_TOKEN="$CONTROL_BOT_TOKEN" OPENCLAW_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" OWNER_ID="$OWNER_ID" \
    TG_CHAT_ID="$TG_CHAT_ID" TG_TOPIC_CODING="$TG_TOPIC_CODING" TG_TOPIC_WRITING="$TG_TOPIC_WRITING" \
    TG_TOPIC_VIDEO="$TG_TOPIC_VIDEO" DASHBOARD_URL="$DASHBOARD_URL" LAUNCHER_URL="$LAUNCHER_URL" \
    node "$APP/launcher.mjs" >>"$LOG/launcher.log" 2>&1 &
}
oc_start() {
  status starting
  if ! node "$APP/render-config.mjs" >>"$LOG/render.log" 2>&1; then status config-error; return 1; fi
  chown -R openclaw:openclaw /home/openclaw/.openclaw /home/openclaw/.drive
  if ! as openclaw OPENCLAW_GATEWAY_TOKEN="$OPENCLAW_GATEWAY_TOKEN" TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
    openclaw config validate >>"$LOG/render.log" 2>&1; then status config-error; return 1; fi
  as openclaw OPENCLAW_GATEWAY_TOKEN="$OPENCLAW_GATEWAY_TOKEN" TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
    NODE_ENV=production openclaw gateway run >>"$LOG/openclaw.log" 2>&1 &
}
alive() { pgrep -u "$1" >/dev/null; }
probe() { curl --fail --silent --show-error --max-time 3 "$1" >/dev/null 2>&1; }
wait_omni() {
  for _ in $(seq 90); do
    [ ! -e /run/onvix.stop ] || exit 0
    touch "$CTL/supervisor-alive"; chmod 644 "$CTL/supervisor-alive"
    if probe http://127.0.0.1:20128/api/monitoring/health; then return 0; fi
    kill -0 "$omni_pid" 2>/dev/null || return 1
    sleep 2
  done
  return 1
}
status stopped
omni_start
if ! wait_omni; then
  echo 'OmniRoute startup failed. Full error: /var/log/agent/omniroute.log' >&2
  exit 1
fi
launcher_start
# Backoff stops permanent configuration/package errors becoming a tight crash loop.
declare -A retry_at=([omniroute]=0 [launcher]=0 [openclaw]=0)
declare -A delay=([omniroute]=5 [launcher]=5 [openclaw]=5)
last_backup=$(date +%s)
last_rotate=$last_backup
while [ ! -e /run/onvix.stop ]; do
  touch "$CTL/supervisor-alive"; chmod 644 "$CTL/supervisor-alive"
  now=$(date +%s)
  if [ -f "$CTL/cmd" ] && mv "$CTL/cmd" "$CTL/cmd.processing"; then
    cmd=$(cat "$CTL/cmd.processing")
    case "$cmd" in
      start|restart) stop_user openclaw; status starting; retry_at[openclaw]=0; delay[openclaw]=5 ;;
      stop) stop_user openclaw; status stopped ;;
    esac
    rm -f "$CTL/cmd.processing"
  fi
  for service in omniroute launcher openclaw; do
    if [ "$service" = openclaw ] && [ ! -f /home/launcher/state/started ]; then
      if alive openclaw; then stop_user openclaw; fi
      status stopped
      continue
    fi
    if ! alive "$service" && (( now >= retry_at[$service] )); then
      case "$service" in
        omniroute) omni_start ;;
        launcher) launcher_start ;;
        openclaw) if probe http://127.0.0.1:20128/api/monitoring/health; then oc_start || true; fi ;;
      esac
      retry_at[$service]=$((now + delay[$service]))
      delay[$service]=$((delay[$service] < 150 ? delay[$service] * 2 : 300))
    fi
  done
  if [ -f /home/launcher/state/started ] && alive openclaw; then
    if probe http://127.0.0.1:18789/readyz; then status running; delay[openclaw]=5
    else status not-ready; fi
  fi
  if [ -n "$backup_pid" ] && ! kill -0 "$backup_pid" 2>/dev/null; then
    if ! wait "$backup_pid"; then echo 'Periodic backup failed; see backup.log' >&2; fi
    backup_pid=''
  fi
  if (( now - last_backup >= 300 )) && [ -z "$backup_pid" ]; then
    backup 9>&- & backup_pid=$!; last_backup=$now
  fi
  if (( now - last_rotate >= 60 )); then
    logrotate -s /var/lib/onvix/logrotate.state "$APP/logrotate.conf" || true
    last_rotate=$now
  fi
  # Never age-delete active workspaces or arbitrary /tmp files.
  sleep 2 & wait $! || true
done
