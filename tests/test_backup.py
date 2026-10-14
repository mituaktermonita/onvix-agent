import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('backup', Path(__file__).resolve().parents[1] / 'app/backup.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.volume = self.root / 'volume'
        self.volume.mkdir()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.sources = {'service': self.source}

    def test_live_wal_committed_rows_restore_without_sidecars(self):
        connection = sqlite3.connect(self.source / 'state.sqlite')
        self.addCleanup(connection.close)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE messages (body TEXT)')
        connection.execute("INSERT INTO messages VALUES ('committed')")
        connection.commit()
        connection.execute("INSERT INTO messages VALUES ('not committed')")
        backup.snapshot(self.volume, self.sources)
        destination = self.root / 'restored'
        backup.restore(self.volume, {'service': destination})
        with sqlite3.connect(destination / 'state.sqlite') as restored:
            self.assertEqual(restored.execute('SELECT * FROM messages').fetchall(), [('committed',)])
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertFalse((destination / 'state.sqlite-wal').exists())

    def test_restore_does_not_overwrite_local_state(self):
        (self.source / 'existing.json').write_text('{}')
        backup.snapshot(self.volume, self.sources)
        with self.assertRaisesRegex(RuntimeError, 'overwrite'):
            backup.restore(self.volume, self.sources)

    def test_only_three_completed_snapshots_retained(self):
        (self.source / 'data').write_text('important')
        for _ in range(5):
            latest = backup.snapshot(self.volume, self.sources)
        self.assertEqual(len(list((self.volume / 'snapshots').iterdir())), 3)
        self.assertEqual((self.volume / 'CURRENT').read_text().strip(), latest)

    def test_incomplete_snapshot_not_advertised(self):
        with self.assertRaises(FileNotFoundError):
            backup.snapshot(self.volume, {'service': self.root / 'missing'})
        self.assertFalse((self.volume / 'CURRENT').exists())
        with self.assertRaisesRegex(RuntimeError, 'No completed snapshot'):
            backup.restore(self.volume, self.sources)

    def test_no_symlink_escape_and_unfinished_work_is_preserved(self):
        (self.source / 'work').mkdir()
        (self.source / 'work' / 'active').write_text('working')
        (self.source / 'link').symlink_to('/etc/passwd')
        name = backup.snapshot(self.volume, self.sources)
        destination = self.volume / 'snapshots' / name / 'service'
        self.assertFalse((destination / 'link').exists())
        self.assertEqual((destination / 'work/active').read_text(), 'working')
        self.assertTrue((self.source / 'work/active').exists())

    def test_invalid_snapshot_pointer_refused(self):
        (self.volume / 'CURRENT').write_text('../outside')
        with self.assertRaises(ValueError):
            backup.restore(self.volume, self.sources)

    def test_original_volume_layout_can_restore(self):
        (self.volume / 'service').mkdir()
        (self.volume / 'service/keys.json').write_text('{}')
        backup.restore(self.volume, self.sources)
        self.assertEqual((self.source / 'keys.json').read_text(), '{}')

    def test_changed_encryption_key_is_rejected(self):
        backup.verify_encryption_key(self.volume, 'original-secret')
        backup.verify_encryption_key(self.volume, 'original-secret')
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            backup.verify_encryption_key(self.volume, 'different-secret')
        self.assertNotIn('original-secret', (self.volume / '.onvix-encryption-key.sha256').read_text())

    def test_missing_service_in_complete_snapshot_is_refused(self):
        name = backup.snapshot(self.volume, self.sources)
        (self.volume / 'snapshots' / name / 'service').rmdir()
        with self.assertRaisesRegex(RuntimeError, 'directory missing'):
            backup.restore(self.volume, self.sources)
