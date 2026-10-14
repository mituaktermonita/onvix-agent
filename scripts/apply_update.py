#!/usr/bin/env python3
"""Apply the extracted Onvix release bundle to an existing local checkout.

Preserves .git, .env, virtual environments and all unlisted files. Makes a sibling
backup of every replaced file first. Does not run git, install dependencies,
change branches, contact the network, or delete project files.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import time
import uuid

PROTECTED = {'.git', '.env', '.venv', '.cache', 'node_modules', '.onvix-deployment.json'}


def safe_relative(name):
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(p in {'..', '.'} for p in path.parts) or '\\' in name:
        raise ValueError(f'Unsafe manifest path: {name}')
    if any(p in PROTECTED or (p.startswith('.env.') and p != '.env.example') for p in path.parts):
        raise ValueError(f'Protected path in manifest: {name}')
    return Path(*path.parts)


def check_destination(target, relative):
    current = target
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f'Refusing to overwrite through a symlink: {relative}')
        if current != target / relative and current.exists() and not current.is_dir():
            raise ValueError(f'Parent path is not a directory: {relative}')
    if current.exists() and not current.is_file():
        raise ValueError(f'Expected a regular file: {relative}')


def apply(bundle, target, dry_run=False):
    bundle, target = bundle.resolve(), target.resolve()
    if not target.is_dir() or not (target / '.git').exists():
        raise ValueError('--target must be your existing Git project directory')
    if target == bundle or bundle in target.parents or target in bundle.parents:
        raise ValueError('Extract the update bundle outside your project before applying')
    manifest = json.loads((bundle / 'MANIFEST.json').read_text())
    if manifest.get('format') != 1:
        raise ValueError('Unsupported bundle manifest')
    layout = manifest.get("payload", "project")
    if layout not in {".", "project"}:
        raise ValueError("Unsupported payload layout")
    source_root = (bundle / layout).resolve()
    plan, seen = [], set()
    for record in manifest['files']:
        name = record['path']
        relative = safe_relative(name)
        if name in seen:
            raise ValueError(f'Duplicate manifest path: {name}')
        seen.add(name)
        source = source_root / relative
        if source.is_symlink() or not source.resolve().is_relative_to(source_root):
            raise ValueError(f'Unsafe source file: {name}')
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != record['sha256']:
            raise ValueError(f'Checksum mismatch: {name}; obtain a fresh archive')
        check_destination(target, relative)
        destination = target / relative
        if name == '.gitignore' and destination.exists():
            # Preserve local-only ignore rules while adding the release's exclusions.
            original = destination.read_text()
            additions = [line for line in content.decode().splitlines() if line not in original.splitlines()]
            content = (original.rstrip('\n') + '\n' + '\n'.join(additions) + '\n').encode() if additions else original.encode()
        if not destination.exists() or destination.read_bytes() != content:
            plan.append((relative, content, 0o755 if name.endswith('.sh') else 0o644))
    print(f'{len(plan)} file(s) will be updated. .env, .git and unlisted files will NOT be touched.')
    for relative, _, _ in plan:
        print('  ' + relative.as_posix())
    if dry_run or not plan:
        return None
    backup = target.parent / f'{target.name}.backup-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}'
    backup.mkdir(mode=0o700)
    existing, created = [], []
    # Back up the entire replacement set before modifying the first project file.
    for relative, _, _ in plan:
        destination = target / relative
        if destination.exists():
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, saved)
            existing.append(relative.as_posix())
        else:
            created.append(relative.as_posix())
    (backup / 'UPDATE-BACKUP.json').write_text(json.dumps({'target': str(target), 'replaced': existing, 'created': created}, indent=2))
    print('Backup:', backup)
    for relative, content, mode in plan:
        check_destination(target, relative)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.onvix-update-', dir=destination.parent)
        try:
            with os.fdopen(descriptor, 'wb') as file:
                file.write(content)
            os.chmod(temporary, mode)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    print('Update applied. Review git diff, commit/push yourself, then follow README-BN.md.')
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        bundle = Path(__file__).resolve().parent
        if not (bundle / "MANIFEST.json").exists() and (bundle.parent / "MANIFEST.json").exists():
            bundle = bundle.parent
        apply(bundle, args.target, args.dry_run)
    except Exception as exc:
        print(f'Update stopped: {exc}. If a backup path was printed, keep it; no project files were deleted.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
