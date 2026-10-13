#!/bin/bash
# Runs as root. Starts OmniRoute -> launcher; OpenClaw starts only on command from the launcher.
set -uo pipefail
set -a; . /run/agent.env; set +a

VOL=/vol
CTL=/run/ctl
LOG=/var/log/agent
APP=/opt/app
OC_HOME=/home/openclaw
PATHV="$PATH"

exec 9>/run/supervisor.lock
flock -n 9 || { echo "supervisor already running"; exit 0; }

mkdir -p "$CTL" "$LOG"
chown launcher:launcher "$CTL"

# local dir <-> volume dir (volume is FUSE, so live DBs stay on local disk and are synced)
SYNC=(
  "omniroute:/home/omniroute/data:$VOL/omniroute"
  "openclaw:$OC_HOME/.openclaw:$VOL/openclaw"
  "launcher:/home/launcher/state:$VOL/launcher"
)

restore() {
  local u l v
  for e in "${SYNC[@]}"; do
    IFS=: read -r u l v <<<"$e"
    mkdir -p "$l"
    [ -d "$v" ] && rsync -a "$v"/ "$l"/
    chown -R "$u:$u" "$l"
  done
}

backup() {
  local u l v
  mountpoint -q "$VOL" || return 0
  for e in "${SYNC[@]}"; do
    IFS=: read -r u l v <<<"$e"
    mkdir -p "$v"
    rsync -a --delete --exclude '*.log' "$l"/ "$v"/ 2>>"$LOG/backup.err"
  done
}

as() { # as <user> <KEY=VAL...> -- <cmd...>   (clean environment per service)
  local u="$1"; shift
  runuser -u "$u" -- env -i PATH="$PATHV" HOME="/home/$u" "$@"
}

omni_start() {
  as omniroute DATA_DIR=/home/omniroute/data PORT=20128 HOSTNAME=0.0.0.0 NODE_ENV=production \
    INITIAL_PASSWORD="$OMNIROUTE_PASSWORD" JWT_SECRET="$JWT_SECRET" API_KEY_SECRET="$API_KEY_SECRET" \
    STORAGE_ENCRYPTION_KEY="$STORAGE_ENCRYPTION_KEY" MACHINE_ID_SALT="$API_KEY_SECRET" \
    OMNIROUTE_WS_BRIDGE_SECRET="$JWT_SECRET" AUTH_COOKIE_SECURE=true REQUIRE_API_KEY=true \
    BASE_URL=http://127.0.0.1:20128 NEXT_PUBLIC_BASE_URL="$DASHBOARD_URL" \
    omniroute >>"$LOG/omniroute.log" 2>&1 &
}

launcher_start() {
  as launcher STATE_DIR=/home/launcher/state CTL_DIR="$CTL" LAUNCHER_TOKEN="$LAUNCHER_TOKEN" \
    CONTROL_BOT_TOKEN="$CONTROL_BOT_TOKEN" OPENCLAW_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" OWNER_ID="$OWNER_ID" \
    TG_CHAT_ID="$TG_CHAT_ID" TG_TOPIC_CODING="$TG_TOPIC_CODING" TG_TOPIC_WRITING="$TG_TOPIC_WRITING" \
    TG_TOPIC_VIDEO="$TG_TOPIC_VIDEO" DASHBOARD_URL="$DASHBOARD_URL" LAUNCHER_URL="$LAUNCHER_URL" \
    GATEWAY_URL="$GATEWAY_URL" node "$APP/launcher.mjs" >>"$LOG/launcher.log" 2>&1 &
}

oc_start() {
  node "$APP/render-config.mjs" >>"$LOG/render.log" 2>&1 || { echo "render failed" >"$CTL/status"; return 1; }
  chown -R openclaw:openclaw "$OC_HOME/.openclaw" "$OC_HOME/.drive" 2>/dev/null
  as openclaw OPENCLAW_GATEWAY_TOKEN="$OPENCLAW_GATEWAY_TOKEN" TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
    NODE_ENV=production openclaw gateway run >>"$LOG/openclaw.log" 2>&1 &
}

janitor() { # age-based, so a running job is never touched; keeps sessions/config/skills
  local m="${JANITOR_MAX_AGE_MIN:-180}"
  find "$OC_HOME"/.openclaw/workspace-*/work -mindepth 1 -mmin +"$m" -delete 2>/dev/null
  find /tmp /var/tmp -xdev -mindepth 1 \( -user openclaw -o -user launcher \) -mmin +"$m" -delete 2>/dev/null
  find "$OC_HOME"/.cache "$OC_HOME"/.npm/_logs -mindepth 1 -mmin +"$m" -delete 2>/dev/null
  for f in "$LOG"/*.log; do [ "$(stat -c %s "$f" 2>/dev/null || echo 0)" -gt 20971520 ] && tail -c 2097152 "$f" >"$f.tmp" && cat "$f.tmp" >"$f" && rm -f "$f.tmp"; done
  true
}

oc_stop() { pkill -TERM -u openclaw; sleep 3; pkill -KILL -u openclaw 2>/dev/null; true; }

alive() { pgrep -u "$1" >/dev/null; }

cleanup() {
  oc_stop; pkill -TERM -u launcher; pkill -TERM -u omniroute
  for _ in $(seq 40); do alive omniroute || break; sleep 1; done
  backup; exit 0
}
trap cleanup TERM INT

restore
omni_start
for _ in $(seq 90); do curl -s -o /dev/null "http://127.0.0.1:20128/" && break; sleep 2; done
launcher_start

want_oc=0
[ -f /home/launcher/state/started ] && want_oc=1 && oc_start
last=$(date +%s); lastj=$last

while true; do
  if [ -f "$CTL/cmd" ]; then
    cmd=$(cat "$CTL/cmd"); rm -f "$CTL/cmd"
    case "$cmd" in
      start|restart) oc_stop; want_oc=1; oc_start ;;
      stop) want_oc=0; oc_stop ;;
    esac
  fi
  alive omniroute || omni_start
  alive launcher  || launcher_start
  if [ "$want_oc" = 1 ] && ! alive openclaw; then sleep 10; oc_start; fi
  if alive openclaw; then echo running >"$CTL/status"; else echo stopped >"$CTL/status"; fi
  now=$(date +%s)
  if [ $((now - last)) -ge 30 ]; then backup; last=$now; fi
  if [ $((now - lastj)) -ge 600 ]; then janitor; lastj=$now; fi
  sleep 2 & wait $!
done
