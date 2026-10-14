#!/usr/bin/env python3
"""Deploy a tested immutable image. Never silently replace/delete an existing sandbox."""
import argparse
from pathlib import Path
import re
import secrets
import shlex
import sys
import time

from daytona import (CreateSandboxFromImageParams, Daytona, DaytonaConfig,
                     DaytonaNotFoundError, Resources, SessionExecuteRequest, VolumeMount)
from dotenv import dotenv_values, set_key

AUTO = {"OMNIROUTE_PASSWORD": 24, "JWT_SECRET": 48, "API_KEY_SECRET": 32,
        "STORAGE_ENCRYPTION_KEY": 32, "LAUNCHER_TOKEN": 32, "OPENCLAW_GATEWAY_TOKEN": 32}
REQUIRED = ["DAYTONA_API_KEY", "TELEGRAM_BOT_TOKEN", "CONTROL_BOT_TOKEN", "OWNER_ID", "TG_CHAT_ID",
            "TG_TOPIC_CODING", "TG_TOPIC_WRITING", "TG_TOPIC_VIDEO", "GDRIVE_CLIENT_ID",
            "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN", "GDRIVE_FOLDER_CODING",
            "GDRIVE_FOLDER_WRITING", "GDRIVE_FOLDER_VIDEO"]


def load_config(path):
    if not path.is_file():
        raise ValueError(f"Copy .env.example to {path} and fill in the values first")
    cfg = dict(dotenv_values(path, interpolate=False))
    missing = [key for key in REQUIRED if not cfg.get(key)]
    if missing:
        raise ValueError("Fill these in .env: " + ", ".join(missing))
    for key in REQUIRED + list(AUTO):
        if any(c in (cfg.get(key) or "") for c in "\r\n\x00"):
            raise ValueError(f"{key} must be a single-line value")
    if not re.fullmatch(r"[1-9]\d*", cfg["OWNER_ID"]):
        raise ValueError("OWNER_ID must be a positive Telegram user ID")
    if not re.fullmatch(r"-100\d+", cfg["TG_CHAT_ID"]):
        raise ValueError("TG_CHAT_ID must be a numeric supergroup ID starting with -100")
    topics = [cfg[f"TG_TOPIC_{name}"] for name in ("CODING", "WRITING", "VIDEO")]
    if any(not re.fullmatch(r"[1-9]\d*", value) or value == "1" for value in topics) or len(set(topics)) != 3:
        raise ValueError("Use three distinct positive topic IDs other than 1 (reserved for General)")
    if cfg["TELEGRAM_BOT_TOKEN"] == cfg["CONTROL_BOT_TOKEN"]:
        raise ValueError("OpenClaw and control must use different Telegram bots")
    path.chmod(0o600)
    for key, size in AUTO.items():
        if not cfg.get(key):
            cfg[key] = secrets.token_urlsafe(size)
            # Appends missing keys too; the old implementation lost newly generated keys.
            set_key(path, key, cfg[key], quote_mode="always")
        elif len(cfg[key]) < 24:
            raise ValueError(f"{key} must be at least 24 characters; do not rotate existing encryption keys blindly")
    path.chmod(0o600)
    return cfg


def validate_image(image):
    # A digest is preferable; sha-<full commit> is also published by our CI.
    if not re.fullmatch(r"ghcr\.io/mituaktermonita/onvix-agent(?::sha-[0-9a-f]{40}|@sha256:[0-9a-f]{64})", image):
        raise ValueError("Use the tested ghcr.io/mituaktermonita/onvix-agent:sha-<40-char-commit> or @sha256:<digest>, not latest")
    return image


def shell_environment(values):
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()).encode()


def checked_exec(sandbox, command, timeout=60):
    response = sandbox.process.exec(command, timeout=timeout)
    if response.exit_code != 0:
        # Do not echo remote output: it can contain credentials or URLs with tokens.
        raise RuntimeError("Remote command failed; inspect sandbox logs. Command: " + command)
    return response.result.strip()


def get_or_create(daytona, args):
    try:
        sandbox = daytona.get(args.name)
    except DaytonaNotFoundError:
        sandbox = None
    # Authentication, network and rate-limit errors MUST NOT fall through into create.
    if sandbox is not None:
        if (sandbox.labels or {}).get("onvix.image") != args.image:
            raise RuntimeError(
                "Existing sandbox uses a different/untracked image. Restarting it will NOT update Node/app files. "
                "Preserve it and its volume; follow README migration steps. No sandbox was deleted."
            )
        if (sandbox.labels or {}).get("onvix.volume") != args.volume:
            raise RuntimeError("Existing sandbox volume does not match --volume")

    volume = daytona.volume.get(args.volume, create=True)
    deadline = time.monotonic() + 120
    while volume.state != "ready":
        if volume.state == "error" or time.monotonic() >= deadline:
            raise RuntimeError("Persistent volume did not become ready")
        time.sleep(2)
        volume = daytona.volume.get(args.volume)
    # Includes legacy sandboxes without our labels. Never mount a volume with another live writer.
    for other in daytona.list():
        if sandbox is not None and other.id == sandbox.id:
            continue
        if other.state in {"stopped", "archived"}:
            continue
        full = daytona.get(other.id)
        if any(mount.volume_id == volume.id for mount in (full.volumes or [])):
            raise RuntimeError(f"Volume is attached to active sandbox {other.name}; stop that writer first")
    if sandbox is not None:
        if not sandbox.public:
            raise RuntimeError("Expected public previews with application auth; sandbox is private")
        if sandbox.state != "started":
            sandbox.start(timeout=180)
        return sandbox
    # No destructive replacement; operator must stop an old writer before reusing its volume.
    return daytona.create(
        CreateSandboxFromImageParams(
            name=args.name, image=args.image, os_user="root",
            labels={"onvix.image": args.image, "onvix.volume": args.volume},
            resources=Resources(cpu=4, memory=8, disk=20),
            auto_stop_interval=0, auto_archive_interval=0, auto_delete_interval=-1,
            public=True,
            volumes=[VolumeMount(volume_id=volume.id, mount_path="/vol")],
        ),
        timeout=600,
    )


def deploy(args, cfg, daytona):
    sandbox = get_or_create(daytona, args)
    if checked_exec(sandbox, "id -u") != "0":
        raise RuntimeError("Sandbox shell must be root")
    checked_exec(sandbox, "node /opt/app/verify-runtime.mjs")
    checked_exec(sandbox, "mountpoint -q /vol")
    sandbox.set_autostop_interval(0)
    links = {key: sandbox.get_preview_link(port).url.rstrip("/") for key, port in
             [("DASHBOARD_URL", 20128), ("LAUNCHER_URL", 8080), ("GATEWAY_URL", 18789)]}
    runtime = {key: cfg[key] for key in REQUIRED + list(AUTO) if key != "DAYTONA_API_KEY"}
    runtime.update(links)
    # Finish the previous supervisor/backup before replacing env or restoring state.
    checked_exec(sandbox, "bash /opt/app/supervisor.sh --stop", timeout=500)
    checked_exec(sandbox, "install -d -m 700 /run/onvix-secrets")
    sandbox.fs.upload_file(shell_environment(runtime), "/run/onvix-secrets/agent.env")
    checked_exec(sandbox, "chmod 600 /run/onvix-secrets/agent.env && mv /run/onvix-secrets/agent.env /run/agent.env")
    session = "onvix-" + secrets.token_hex(6)
    sandbox.process.create_session(session)
    sandbox.process.execute_session_command(
        session, SessionExecuteRequest(command="bash /opt/app/supervisor.sh >>/var/log/supervisor.out 2>&1", run_async=True),
    )
    checked_exec(sandbox, "bash /opt/app/healthcheck.sh --wait 240", timeout=260)
    print(f"Sandbox ready: {args.name}\nImage: {args.image}")
    print(f"OmniRoute: {links['DASHBOARD_URL']}\nLauncher: {links['LAUNCHER_URL']}\nOpenClaw: {links['GATEWAY_URL']}")
    print("Credentials remain in your local .env. The control bot sends the tokenized setup link.")
    print("OpenClaw starts after API keys are configured; model/Telegram/Drive credentials still need an end-to-end test.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=validate_image)
    parser.add_argument("--name", default="onvix-agents")
    parser.add_argument("--volume", default="onvix-agents-data")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).with_name(".env"))
    args = parser.parse_args()
    try:
        cfg = load_config(args.env_file)
        deploy(args, cfg, Daytona(DaytonaConfig(api_key=cfg["DAYTONA_API_KEY"])))
    except (ValueError, RuntimeError) as exc:
        print(f"Deployment stopped: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Deployment stopped ({type(exc).__name__}). Check connectivity, Daytona permissions/quota and sandbox logs; no automatic deletion was attempted.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
