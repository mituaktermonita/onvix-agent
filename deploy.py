#!/usr/bin/env python3
"""Create (or restart) the Onvix agent sandbox on Daytona.  pip install daytona ; python deploy.py"""
import os, secrets, sys, time
from pathlib import Path

from daytona import (CreateSandboxFromImageParams, Daytona, DaytonaConfig,
                     Resources, SessionExecuteRequest, VolumeMount)

NAME = "onvix-agents"
VOLUME = "onvix-agents-data"
IMAGE = "ghcr.io/mituaktermonita/onvix-agent:latest"
ENV = Path(__file__).with_name(".env")

AUTO = {"OMNIROUTE_PASSWORD": 18, "JWT_SECRET": 48, "API_KEY_SECRET": 32,
        "STORAGE_ENCRYPTION_KEY": 32, "LAUNCHER_TOKEN": 24, "OPENCLAW_GATEWAY_TOKEN": 32}
REQUIRED = ["DAYTONA_API_KEY", "TELEGRAM_BOT_TOKEN", "CONTROL_BOT_TOKEN", "OWNER_ID", "TG_CHAT_ID",
            "TG_TOPIC_CODING", "TG_TOPIC_WRITING", "TG_TOPIC_VIDEO", "GDRIVE_CLIENT_ID",
            "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN", "GDRIVE_FOLDER_CODING",
            "GDRIVE_FOLDER_WRITING", "GDRIVE_FOLDER_VIDEO"]

cfg = {}
for line in ENV.read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip()

missing = [k for k in REQUIRED if not cfg.get(k)]
if missing:
    sys.exit("Fill these in .env first: " + ", ".join(missing))

lines = ENV.read_text().splitlines()
for k, n in AUTO.items():
    if not cfg.get(k):
        cfg[k] = secrets.token_urlsafe(n)
        lines = [f"{k}={cfg[k]}" if l.startswith(k + "=") else l for l in lines]
ENV.write_text("\n".join(lines) + "\n")

daytona = Daytona(DaytonaConfig(api_key=cfg["DAYTONA_API_KEY"]))

volume = daytona.volume.get(VOLUME, create=True)
while volume.state != "ready":
    time.sleep(1)
    volume = daytona.volume.get(VOLUME)

try:
    sandbox = daytona.get(NAME)
    if sandbox.state != "started":
        sandbox.start()
except Exception:
    # Secrets are NOT stored in the sandbox config, image, repo or volume: they go only into
    # /run/agent.env (below) on every run of this script.
    sandbox = daytona.create(
        CreateSandboxFromImageParams(
            name=NAME,
            image=IMAGE,
            resources=Resources(cpu=4, memory=8, disk=10),
            auto_stop_interval=0,
            public=True,
            volumes=[VolumeMount(volume_id=volume.id, mount_path="/vol")],
        ),
        timeout=300,
        on_snapshot_create_logs=lambda c: print(c, end=""),
    )

sandbox.set_autostop_interval(0)
dash = sandbox.get_preview_link(20128).url.rstrip("/")
launch = sandbox.get_preview_link(8080).url.rstrip("/")
gate = sandbox.get_preview_link(18789).url.rstrip("/")

if sandbox.process.exec("id -u").result.strip() != "0":
    sys.exit("sandbox shell is not root; supervisor needs root")

# hand the supervisor its environment through a root-only file (does not rely on env inheritance)
runtime = {k: v for k, v in cfg.items() if k != "DAYTONA_API_KEY"}
runtime.update(DASHBOARD_URL=dash, LAUNCHER_URL=launch, GATEWAY_URL=gate)
names = " ".join(runtime)
sandbox.process.exec(
    "bash -c 'umask 077; for k in " + names + "; do printf \"%s=%q\\n\" $k \"${!k}\"; done > /run/agent.env'",
    env=runtime,
)

try:
    sandbox.process.create_session("supervisor")
except Exception:
    pass
sandbox.process.execute_session_command(
    "supervisor",
    SessionExecuteRequest(command="nohup bash /opt/app/supervisor.sh >/var/log/supervisor.out 2>&1 &", run_async=True),
)

print(f"\nSandbox: {NAME}\nOmniRoute dashboard: {dash}\nOmniRoute login password: {cfg['OMNIROUTE_PASSWORD']}\n"
      f"OpenClaw dashboard: {gate}\nOpenClaw gateway token: {cfg['OPENCLAW_GATEWAY_TOKEN']}\n"
      "Telegram control bot will message you shortly.")
