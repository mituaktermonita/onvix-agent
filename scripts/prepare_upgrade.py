#!/usr/bin/env python3
"""Explicitly stop + snapshot an existing sandbox; keep the old sandbox for recovery."""
import argparse
import hashlib
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy import checked_exec, load_config  # noqa: E402
from daytona import Daytona, DaytonaConfig  # noqa: E402


def prepare(args, cfg, client):
    sandbox = client.get(args.name)
    if sandbox.state != 'started':
        raise RuntimeError('Start the source sandbox first so its local state can be snapshotted')
    volume = client.volume.get(args.volume)
    if not any(mount.volume_id == volume.id and mount.mount_path == '/vol' for mount in (sandbox.volumes or [])):
        raise RuntimeError('Source sandbox does not mount the requested volume at /vol')
    for other in client.list():
        if other.id == sandbox.id or other.state in {'stopped', 'archived'}:
            continue
        full = client.get(other.id)
        if any(mount.volume_id == volume.id for mount in (full.volumes or [])):
            raise RuntimeError('Another active sandbox uses this volume; stop it first')
    if checked_exec(sandbox, 'id -u') != '0':
        raise RuntimeError('Source sandbox shell must be root')
    checked_exec(sandbox, 'mountpoint -q /vol && test -s /run/agent.env')
    for key in ['STORAGE_ENCRYPTION_KEY', 'API_KEY_SECRET']:
        code = f"import os,hashlib; print(hashlib.sha256(os.environ[{key!r}].encode()).hexdigest())"
        command = 'set -a; . /run/agent.env; python3 -c ' + shlex.quote(code)
        actual = checked_exec(sandbox, 'bash -c ' + shlex.quote(command))
        if actual != hashlib.sha256(cfg[key].encode()).hexdigest():
            raise RuntimeError(f'{key} differs from the old sandbox. Restore the original .env before upgrading')
    checked_exec(sandbox, 'install -d -m 700 /run/onvix-migrate')
    for name in ['quiesce.py', 'backup.py']:
        sandbox.fs.upload_file((ROOT / 'app' / name).read_bytes(), '/run/onvix-migrate/' + name)
    checked_exec(sandbox, 'python3 /run/onvix-migrate/quiesce.py', timeout=320)
    # Inherit the ORIGINAL sandbox encryption key. No credentials enter command strings.
    command = ('set -a; . /run/agent.env; '
               'python3 /run/onvix-migrate/backup.py verify-key && '
               'timeout 600 python3 /run/onvix-migrate/backup.py backup')
    checked_exec(sandbox, 'bash -c ' + shlex.quote(command), timeout=620)
    checked_exec(sandbox, 'test -s /vol/CURRENT')
    sandbox.stop(timeout=180)
    print(f'Upgrade snapshot complete. Old sandbox {args.name} is stopped, NOT deleted.')
    print(f'Next: deploy the tested image under a NEW --name and reuse --volume {args.volume}.')
    print('Do not restart the old sandbox while the new sandbox uses this volume.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', default='onvix-agents')
    parser.add_argument('--volume', default='onvix-agents-data')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--confirm-stop', action='store_true', help='Confirm all active agent work may be interrupted')
    args = parser.parse_args()
    if not args.confirm_stop:
        parser.error('Wait for active work to finish, then use --confirm-stop')
    try:
        cfg = load_config(args.env_file)
        prepare(args, cfg, Daytona(DaytonaConfig(api_key=cfg['DAYTONA_API_KEY'])))
    except Exception as exc:
        # SDK exceptions can embed URLs or credentials: only our own errors are printed verbatim.
        detail = str(exc) if type(exc) in {ValueError, RuntimeError} else type(exc).__name__
        print(f'Upgrade preparation stopped: {detail}\nKeep the source sandbox and volume. Inspect logs before retrying.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
