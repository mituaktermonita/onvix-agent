import shutil
import os
def _replace(src, dst):
    shutil.copyfile(src, dst)
    os.remove(src)
#!/usr/bin/env python3
"""Single-writer volume snapshots; SQLite online backup, never raw live WAL copies.

Each DB is consistent independently; a live snapshot is not a transaction across
all services. Quiesce services for a final migration snapshot. Keep the .env
(encryption keys) separately; the volume also contains sensitive application data.
"""
import argparse
from contextlib import closing
import json
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import time
import tempfile
import uuid

SOURCES = {
    "omniroute": Path("/home/omniroute/data"),
    "openclaw": Path("/home/openclaw/.openclaw"),
    "launcher": Path("/home/launcher/state"),
}
SKIP = {"node_modules", ".cache", ".npm", "logs"}


def copy_tree(source, target, online=False):
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    for entry in source.iterdir():
        if entry.name in SKIP or entry.is_symlink() or entry.name.endswith((".log", ".tmp")):
            continue
        dest = target / entry.name
        if entry.is_dir():
            copy_tree(entry, dest, online)
        elif entry.is_file():
            if online and entry.name.endswith(("-wal", "-shm", "-journal")):
                continue
            try:
                with entry.open("rb") as f:
                    is_sqlite = f.read(16) == b"SQLite format 3\x00"
                if online and is_sqlite:
                    deadline = time.monotonic() + 30
                    def progress(*_):
                        if time.monotonic() > deadline:
                            raise TimeoutError(f"SQLite snapshot timed out: {entry.name}")
                    # Never open SQLite on Daytona's FUSE/S3 mount. Produce a closed,
                    # single-file DB locally, then copy the bytes to the volume.
                    with tempfile.TemporaryDirectory(prefix="onvix-db-") as temporary:
                        local = Path(temporary) / "snapshot.sqlite"
                        with closing(sqlite3.connect(entry.as_uri() + "?mode=ro", uri=True, timeout=10)) as src:
                            with closing(sqlite3.connect(local)) as dst:
                                src.backup(dst, pages=256, progress=progress)
                                dst.execute("PRAGMA journal_mode=DELETE")
                                if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                                    raise RuntimeError(f"Invalid backup: {entry.name}")
                        shutil.copyfile(local, dest)
                else:
                    shutil.copyfile(entry, dest)
                dest.chmod(0o600)
            except FileNotFoundError:
                # A transient non-DB file may disappear while a live snapshot runs.
                if not online:
                    raise


def verify_encryption_key(volume, secret):
    if not secret:
        raise ValueError("Missing STORAGE_ENCRYPTION_KEY")
    fingerprint = hashlib.sha256(secret.encode()).hexdigest()
    marker = volume / ".onvix-encryption-key.sha256"
    if marker.exists():
        if marker.read_text().strip() != fingerprint:
            raise RuntimeError("STORAGE_ENCRYPTION_KEY changed. Restore the original local .env; do not overwrite the volume")
    else:
        temporary = volume / ".encryption-key.tmp"
        temporary.write_text(fingerprint + "\n")
        _replace(temporary, marker)


def snapshot(volume, sources=SOURCES):
    snapshots = volume / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = f"{time.time_ns()}-{uuid.uuid4().hex}"
    target = snapshots / name
    target.mkdir(mode=0o700)
    # Incomplete snapshots are never advertised or restored.
    for service, source in sources.items():
        copy_tree(source, target / service, online=True)
    (target / "complete.json").write_text(json.dumps({"version": 1, "services": list(sources)}))
    temporary = volume / ".CURRENT.tmp"
    temporary.write_text(name + "\n")
    _replace(temporary, volume / "CURRENT")
    complete = sorted(p for p in snapshots.iterdir() if p.is_dir() and (p / "complete.json").exists())
    for old in complete[:-3]:
        shutil.rmtree(old)
    return name


def restore(volume, sources=SOURCES):
    marker = volume / "CURRENT"
    if marker.exists():
        name = marker.read_text().strip()
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("Invalid snapshot pointer")
        origin = volume / "snapshots" / name
        if not (origin / "complete.json").is_file():
            raise RuntimeError("Snapshot incomplete; refusing restore")
        manifest = json.loads((origin / "complete.json").read_text())
        if manifest.get("version") != 1 or set(manifest.get("services", [])) != set(sources):
            raise RuntimeError("Snapshot manifest mismatch; refusing restore")
        if any(not (origin / service).is_dir() for service in sources):
            raise RuntimeError("Snapshot service directory missing; refusing restore")
    else:
        if (volume / "snapshots").exists() and any((volume / "snapshots").iterdir()):
            raise RuntimeError("No completed snapshot; inspect volume before restoring")
        # Backward compatibility with the original /vol/<service> layout.
        origin = volume
    for service, destination in sources.items():
        if destination.exists() and any(destination.iterdir()):
            raise RuntimeError(f"Refusing to overwrite existing local state: {destination}")
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        if (origin / service).exists():
            copy_tree(origin / service, destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore", "verify-key"])
    parser.add_argument("--volume", type=Path, default=Path("/vol"))
    args = parser.parse_args()
    if not os.path.ismount(args.volume):
        parser.error("Persistent volume is not mounted; refusing to write an ephemeral backup")
    os.umask(0o077)
    if args.action == "verify-key":
        verify_encryption_key(args.volume, os.environ.get("STORAGE_ENCRYPTION_KEY"))
    elif args.action == "backup":
        print("Backup complete:", snapshot(args.volume))
    else:
        restore(args.volume)
