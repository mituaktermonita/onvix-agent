#!/usr/bin/env python3
"""Stop an old or current Onvix supervisor before an explicit upgrade snapshot.

Only used by scripts/prepare_upgrade.py after operator confirmation. Never deletes
state. Fail rather than snapshot while any application writer is still running.
"""
import os
from pathlib import Path
import pwd
import signal
import time


def processes():
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            fields = (proc / 'status').read_text().splitlines()
            state = next(line for line in fields if line.startswith('State:')).split()[1]
            if state == 'Z':
                continue
            uid = int(next(line for line in fields if line.startswith('Uid:')).split()[1])
            argv = (proc / 'cmdline').read_bytes().split(b'\0')
            yield int(proc.name), uid, argv
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue


def main():
    if os.getuid() != 0:
        raise RuntimeError('Upgrade preparation requires root')
    users = {pwd.getpwnam(name).pw_uid for name in ['openclaw', 'launcher', 'omniroute']}
    supervisors = {pid for pid, uid, argv in processes()
                   if uid == 0 and b'/opt/app/supervisor.sh' in argv
                   and b'--stop' not in argv and pid != os.getpid()}
    for pid in supervisors:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 240
    while supervisors & {pid for pid, _, _ in processes()}:
        if time.monotonic() >= deadline:
            raise RuntimeError('Supervisor shutdown timed out. Inspect its logs; no upgrade snapshot was taken')
        time.sleep(1)
    # Stop orphaned service workers as well. No forced KILL: a timeout requires inspection.
    for pid, uid, _ in processes():
        if uid in users:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 60
    while any(uid in users for _, uid, _ in processes()):
        if time.monotonic() >= deadline:
            raise RuntimeError('Service workers remain active; inspect before retrying upgrade')
        time.sleep(1)
    print('Application writers stopped; local state preserved.')


if __name__ == '__main__':
    main()
