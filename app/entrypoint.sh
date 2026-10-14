#!/bin/bash
set -euo pipefail
if [ -s /run/agent.env ]; then
  exec bash /opt/app/supervisor.sh
fi
# Daytona may replace the image entrypoint. deploy.py explicitly starts the supervisor.
# A local container needs --env-file plus a root-only /run/agent.env bind mount.
exec sleep infinity
