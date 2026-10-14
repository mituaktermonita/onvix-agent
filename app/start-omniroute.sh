#!/bin/bash
set -euo pipefail
# Avoid loading the CLI's entire TS/tsx/Commander graph just to run a prebuilt server.
# Keep the WS wrapper: launching server.js alone loses the Responses WS proxy.
root="${RUNTIME_ROOT:-/opt/runtime}/node_modules/omniroute/dist"
cd "$root"
export NODE_ENV=production HOSTNAME=0.0.0.0 OMNIROUTE_SERVER_HOST=0.0.0.0
export PORT="${PORT:-20128}"
export OMNIROUTE_PORT="$PORT"
export DASHBOARD_PORT="$PORT" API_PORT="$PORT"
export NEXT_TELEMETRY_DISABLED=1
export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=2048}"
exec node "$root/server-ws.mjs"
