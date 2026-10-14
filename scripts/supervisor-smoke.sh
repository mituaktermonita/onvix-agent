#!/bin/bash
# Destructive only to synthetic state inside a fresh, disposable test container.
# CI mounts an empty tmpfs at /vol. Never run this on a real sandbox.
set -Eeuo pipefail
[ "${ONVIX_DISPOSABLE_TEST:-}" = 1 ] || { echo 'Disposable test container required'; exit 1; }
[ ! -e /run/agent.env ] && [ ! -e /var/lib/onvix/restored ]
mountpoint -q /vol
cleanup() {
  code=$?
  trap - EXIT INT TERM
  bash /opt/app/supervisor.sh --stop || true
  if [ "$code" != 0 ]; then
    tail -n 100 /var/log/supervisor.out /var/log/agent/*.log 2>/dev/null || true
  fi
  exit "$code"
}
trap cleanup EXIT
trap 'exit 1' INT TERM
python3 - <<'PY'
import secrets, shlex
from pathlib import Path
values = {k: secrets.token_hex(32) for k in (
    'OMNIROUTE_PASSWORD', 'JWT_SECRET', 'API_KEY_SECRET', 'STORAGE_ENCRYPTION_KEY',
    'LAUNCHER_TOKEN', 'OPENCLAW_GATEWAY_TOKEN')}
values.update(TELEGRAM_BOT_TOKEN='123456:fixture', CONTROL_BOT_TOKEN='654321:fixture',
              OWNER_ID='123456789', TG_CHAT_ID='-1001234567890', TG_TOPIC_CODING='2',
              TG_TOPIC_WRITING='3', TG_TOPIC_VIDEO='4',
              DASHBOARD_URL='https://omni.example.invalid', LAUNCHER_URL='https://launcher.example.invalid',
              GATEWAY_URL='https://gateway.example.invalid')
file = Path('/run/agent.env')
file.write_text(''.join(f'{k}={shlex.quote(v)}\n' for k,v in values.items()))
file.chmod(0o600)
PY
bash /opt/app/supervisor.sh >/var/log/supervisor.out 2>&1 &
pid=$!
bash /opt/app/healthcheck.sh --wait 120
# A second supervisor must not take ownership or start duplicate services.
bash /opt/app/supervisor.sh
printf 'preserve-local-state\n' > /home/launcher/state/test-marker
bash /opt/app/supervisor.sh --stop
wait "$pid"
[ -s /vol/CURRENT ]
[ "$(cat /run/onvix-stop-result)" = 0 ]
if pgrep -u omniroute >/dev/null; then exit 1; fi
if pgrep -u launcher >/dev/null; then exit 1; fi
# Restart should not restore an older volume over newer local files.
printf 'newer-local-state\n' > /home/launcher/state/test-marker
bash /opt/app/supervisor.sh >>/var/log/supervisor.out 2>&1 &
pid=$!
bash /opt/app/healthcheck.sh --wait 120
[ "$(cat /home/launcher/state/test-marker)" = newer-local-state ]
bash /opt/app/supervisor.sh --stop
wait "$pid"
python3 - <<'PY'
from pathlib import Path
volume = Path('/vol')
current = volume / 'snapshots' / (volume / 'CURRENT').read_text().strip()
assert (current / 'launcher/test-marker').read_text().strip() == 'newer-local-state'
assert (current / 'complete.json').is_file()
PY
echo 'PASS: supervisor startup, singleton lock, graceful shutdown, final backup and local-state restart.'
