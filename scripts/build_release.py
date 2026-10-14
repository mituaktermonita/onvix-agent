#!/usr/bin/env python3
"""Build a source-only update ZIP, never including .env, .git or dependency caches."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = ['Dockerfile', '.dockerignore', '.gitignore', '.env.example', 'deploy.py',
              'requirements.txt', 'README.md', 'README-BN.md',
              'runtime/package.json', 'runtime/package-lock.json']
DIRECTORIES = ['.github', 'app', 'scripts', 'tests', 'docs']
EXTENSIONS = {'.yml', '.yaml', '.mjs', '.py', '.sh', '.md', '.conf'}

START = '''Onvix Agent — source update package

১. এই ZIP আপনার project-এর বাইরে extract করুন।
২. Extract করা onvix-agent-ready folder-এ terminal খুলুন।
৩. নিজের project path বসিয়ে চালান:

python3 apply_update.py --target /path/to/onvix-agent --dry-run
python3 apply_update.py --target /path/to/onvix-agent

Windows-এ python3-এর বদলে python ব্যবহার করতে পারেন। Python 3.11+ লাগবে।

আপনার .env এবং .git অক্ষত থাকবে। Replace করা files-এর backup project-এর পাশে
একটি আলাদা folder-এ থাকবে। অচেনা files delete করা হবে না। Git push নিজে করবেন।

এরপর updated project-এর README-BN.md অনুসরণ করুন:
- git diff review → commit/push → main-এর tested-image workflow সফল হওয়া পর্যন্ত অপেক্ষা;
- existing .env অক্ষত রেখে requirements.txt install;
- পুরোনো sandbox থাকলে explicit prepare_upgrade.py command;
- workflow summary-র আসল immutable image reference দিয়ে deploy.py চালান।

পুরোনো sandbox restart করলেই নতুন Docker image আসে না। পুরোনো ও নতুন sandbox
একই volume ব্যবহার করলে একসঙ্গে চালু করবেন না। Package কখনও sandbox delete করে না।

এই archive source release; prebuilt Docker image নয়। 29 Python ও 6 JavaScript tests,
বাস্তব local service startup ও supervisor lifecycle পরীক্ষা হয়েছে। Real Daytona,
Telegram/Drive/provider credentials এবং Docker build এখনও আপনার CI/deployment-এ
যাচাই করতে হবে। docs/VALIDATION.md-এ পরীক্ষার সীমা লেখা আছে।

প্রথমে README-BN.md পড়তে পারেন। Secret/credentials chat বা Git-এ দেবেন না।
'''


def build(output):
    paths = {ROOT / name for name in ROOT_FILES}
    for directory in DIRECTORIES:
        paths.update(path for path in (ROOT / directory).rglob('*')
                     if path.is_file() and path.suffix in EXTENSIONS and '__pycache__' not in path.parts)
    records, payload = [], {}
    for path in sorted(paths):
        if path.is_symlink():
            raise ValueError(f'Symlink is not allowed in release: {path}')
        relative = path.relative_to(ROOT).as_posix()
        content = path.read_bytes()
        records.append({'path': relative, 'sha256': hashlib.sha256(content).hexdigest()})
        payload[relative] = content
    manifest = {'format': 1, 'payload': '.', 'built_at': datetime.now(timezone.utc).isoformat(), 'files': records}
    payload['MANIFEST.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    payload['apply_update.py'] = (ROOT / 'scripts/apply_update.py').read_bytes()
    payload['START-HERE-BN.txt'] = START.encode()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(payload.items()):
            info = zipfile.ZipInfo('onvix-agent-ready/' + name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | (0o755 if name.endswith('.sh') else 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    print(f'{output}\n{len(records)} project files; {output.stat().st_size:,} bytes')
    print('SHA256:', hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    build(parser.parse_args().output)
