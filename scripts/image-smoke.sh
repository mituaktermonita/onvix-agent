#!/bin/bash
# Runs inside the candidate image in CI, or against the locked runtime locally.
set -Eeuo pipefail
APP_DIR=${APP_DIR:-/opt/app}
RUNTIME_ROOT=${RUNTIME_ROOT:-/opt/runtime}
TEST_DIR=${TEST_DIR:-/opt/tests}
export APP_DIR RUNTIME_ROOT
export PATH="$RUNTIME_ROOT/node_modules/.bin:$PATH"
tmp=$(mktemp -d)
chmod 755 "$tmp"
pids=()
cleanup() {
  code=$?
  trap - EXIT INT TERM
  for pid in "${pids[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || true; done
  for _ in $(seq 30); do
    alive=0; for pid in "${pids[@]}"; do kill -0 -- "-$pid" 2>/dev/null && alive=1 || true; done
    [ "$alive" = 1 ] || break
    sleep 1
  done
  for pid in "${pids[@]}"; do kill -KILL -- "-$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  if [ "$code" != 0 ]; then
    for log in "$tmp"/*.log; do [ ! -f "$log" ] || { echo "--- $log"; tail -n 100 "$log"; }; done
  fi
  rm -rf "$tmp"
  exit "$code"
}
trap cleanup EXIT
trap 'exit 1' INT TERM
as() {
  user=$1; shift
  # Never inherit a root-only working directory from the calling shell.
  cd /tmp
  if [ "$(id -u)" = 0 ]; then exec setsid runuser -u "$user" -- env -i PATH="$PATH" "$@"
  else exec setsid env -i PATH="$PATH" "$@"; fi
}
wait_url() {
  local url=$1 pid=$2
  for _ in $(seq 120); do
    if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
      kill -0 "$pid" 2>/dev/null || return 1
      return 0
    fi
    kill -0 "$pid" 2>/dev/null || { echo "Process exited before readiness: $url"; return 1; }
    sleep 1
  done
  echo "Readiness timeout: $url"; return 1
}
# Refuse false positives from another service already occupying a test port.
node --input-type=module <<'JS'
import net from 'node:net';
for (const port of [20128, 18789, 8080]) {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '0.0.0.0', resolve); });
  await new Promise(resolve => server.close(resolve));
}
JS
node "${VERIFY_SCRIPT:-$APP_DIR/verify-runtime.mjs}"
node "$TEST_DIR/image-config.mjs" "$tmp"
token=$(cat "$tmp/gateway-token")
export OPENCLAW_GATEWAY_TOKEN="$token" TELEGRAM_BOT_TOKEN='123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
mkdir -p "$tmp/omni/data" "$tmp/launcher/state" "$tmp/launcher/ctl"
if [ "$(id -u)" = 0 ]; then
  chown -R openclaw:openclaw "$tmp/openclaw"
  chown -R omniroute:omniroute "$tmp/omni"
  chown -R launcher:launcher "$tmp/launcher"
fi
(as openclaw HOME="$tmp/openclaw" OPENCLAW_GATEWAY_TOKEN="$token" TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
  openclaw config validate) >"$tmp/config.log" 2>&1
# Validate the real Telegram config above; disable network channels only for offline boot.
node --input-type=module - "$tmp/openclaw/.openclaw/openclaw.json" <<'JS'
import fs from 'node:fs';
const file = process.argv[2], cfg = JSON.parse(fs.readFileSync(file, 'utf8'));
cfg.channels.telegram.enabled = false;
fs.writeFileSync(file, JSON.stringify(cfg));
JS
as omniroute HOME="$tmp/omni" DATA_DIR="$tmp/omni/data" RUNTIME_ROOT="$RUNTIME_ROOT" \
  INITIAL_PASSWORD="$token" JWT_SECRET="$token" API_KEY_SECRET="$token" STORAGE_ENCRYPTION_KEY="$token" \
  REQUIRE_API_KEY=true AUTH_COOKIE_SECURE=true bash "$APP_DIR/start-omniroute.sh" >"$tmp/omni.log" 2>&1 &
pids+=("$!")
wait_url http://127.0.0.1:20128/api/monitoring/health "${pids[0]}"
curl -fsS --max-time 10 http://127.0.0.1:20128/login >/dev/null
[ "$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:20128/v1/models)" = 401 ]
as openclaw HOME="$tmp/openclaw" OPENCLAW_GATEWAY_TOKEN="$token" TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
  openclaw gateway run >"$tmp/openclaw.log" 2>&1 &
pids+=("$!")
wait_url http://127.0.0.1:18789/readyz "${pids[1]}"
as launcher HOME="$tmp/launcher" STATE_DIR="$tmp/launcher/state" CTL_DIR="$tmp/launcher/ctl" \
  LAUNCHER_TOKEN="$token" LAUNCHER_URL=https://launcher.example.invalid DISABLE_TELEGRAM=1 \
  node "$APP_DIR/launcher.mjs" >"$tmp/launcher.log" 2>&1 &
pids+=("$!")
wait_url http://127.0.0.1:8080/healthz "${pids[2]}"
[ "$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/)" = 403 ]
curl -fsS --max-time 3 "http://127.0.0.1:8080/?t=$token" >/dev/null
echo 'PASS: runtime engines, rendered config schema, OmniRoute HTTP/auth, OpenClaw readiness, launcher auth.'
