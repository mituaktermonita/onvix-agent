from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]

def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

updater = module('updater', 'scripts/apply_update.py')
upgrade = module('upgrade', 'scripts/prepare_upgrade.py')


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'bundle'
        self.target = self.root / 'project'
        (self.bundle / 'project').mkdir(parents=True)
        (self.target / '.git').mkdir(parents=True)
        (self.target / '.git/HEAD').write_text('keep-branch')
        (self.target / '.env').write_text('PRIVATE=keep-secret\n')
        (self.target / 'Dockerfile').write_text('old')
        self.manifest({'Dockerfile': 'new', 'app/start.sh': '#!/bin/bash\necho start\n'})

    def manifest(self, files):
        records = []
        for name, text in files.items():
            destination = self.bundle / 'project' / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text)
            records.append({'path': name, 'sha256': hashlib.sha256(text.encode()).hexdigest()})
        (self.bundle / 'MANIFEST.json').write_text(json.dumps({'format': 1, 'files': records}))

    def apply(self, dry=False):
        with redirect_stdout(io.StringIO()):
            return updater.apply(self.bundle, self.target, dry)

    def test_update_preserves_secrets_git_unknown_files_and_backs_up(self):
        (self.target / 'my-notes.txt').write_text('keep')
        saved = self.apply()
        self.assertEqual((self.target / 'Dockerfile').read_text(), 'new')
        self.assertEqual((saved / 'Dockerfile').read_text(), 'old')
        self.assertEqual((self.target / '.env').read_text(), 'PRIVATE=keep-secret\n')
        self.assertEqual((self.target / '.git/HEAD').read_text(), 'keep-branch')
        self.assertEqual((self.target / 'my-notes.txt').read_text(), 'keep')
        self.assertEqual((self.target / 'app/start.sh').stat().st_mode & 0o777, 0o755)
        self.assertIsNone(self.apply())

    def test_dry_run_makes_no_changes_or_backups(self):
        self.apply(True)
        self.assertEqual((self.target / 'Dockerfile').read_text(), 'old')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['bundle', 'project'])

    def test_corrupt_bundle_refused_before_writing(self):
        (self.bundle / 'project/Dockerfile').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
            self.apply()
        self.assertEqual((self.target / 'Dockerfile').read_text(), 'old')

    def test_symlink_destination_refused(self):
        (self.target / 'app').symlink_to(self.root / 'elsewhere')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.apply()
        self.assertEqual((self.target / 'Dockerfile').read_text(), 'old')

    def test_protected_paths_refused(self):
        for name in ['.env', '.git/config', '../outside', '/absolute', '.env.production']:
            with self.subTest(path=name), self.assertRaises(ValueError):
                updater.safe_relative(name)

    def test_existing_ignore_rules_are_preserved(self):
        (self.target / '.gitignore').write_text('my-private-file\n')
        self.manifest({'.gitignore': '.env\nnode_modules/\n'})
        self.apply()
        result = (self.target / '.gitignore').read_text()
        self.assertIn('my-private-file', result)
        self.assertIn('.env', result)


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {'STORAGE_ENCRYPTION_KEY': 'original-key', 'API_KEY_SECRET': 'original-api'}
        self.args = SimpleNamespace(name='old', volume='data')
        self.sandbox = Mock(state='started', id='old-id', volumes=[SimpleNamespace(volume_id='vol', mount_path='/vol')])
        self.client = Mock()
        self.client.get.return_value = self.sandbox
        self.client.volume.get.return_value = SimpleNamespace(id='vol')
        self.client.list.return_value = []

    def answer(self, sandbox, command, **kwargs):
        if command == 'id -u':
            return '0'
        for key in self.cfg:
            if key in command:
                return hashlib.sha256(self.cfg[key].encode()).hexdigest()
        return ''

    def test_success_snapshots_before_stopping_and_never_deletes(self):
        commands = []
        def execute(sandbox, command, **kwargs):
            commands.append(command)
            return self.answer(sandbox, command, **kwargs)
        with patch.object(upgrade, 'checked_exec', side_effect=execute), redirect_stdout(io.StringIO()):
            upgrade.prepare(self.args, self.cfg, self.client)
        self.assertTrue(any('quiesce.py' in cmd for cmd in commands))
        self.assertTrue(any('backup.py backup' in cmd for cmd in commands))
        self.sandbox.stop.assert_called_once_with(timeout=180)
        self.sandbox.delete.assert_not_called()
        self.assertEqual(self.sandbox.fs.upload_file.call_count, 2)

    def test_key_mismatch_does_not_interrupt_source(self):
        def execute(sandbox, command, **kwargs):
            if 'STORAGE_ENCRYPTION_KEY' in command:
                return 'wrong-fingerprint'
            return self.answer(sandbox, command, **kwargs)
        with patch.object(upgrade, 'checked_exec', side_effect=execute):
            with self.assertRaisesRegex(RuntimeError, 'differs'):
                upgrade.prepare(self.args, self.cfg, self.client)
        self.sandbox.stop.assert_not_called()
        self.sandbox.fs.upload_file.assert_not_called()

    def test_snapshot_failure_does_not_stop_or_delete_sandbox(self):
        def execute(sandbox, command, **kwargs):
            if 'backup.py backup' in command:
                raise RuntimeError('snapshot failed')
            return self.answer(sandbox, command, **kwargs)
        with patch.object(upgrade, 'checked_exec', side_effect=execute):
            with self.assertRaisesRegex(RuntimeError, 'snapshot failed'):
                upgrade.prepare(self.args, self.cfg, self.client)
        self.sandbox.stop.assert_not_called()
        self.sandbox.delete.assert_not_called()
