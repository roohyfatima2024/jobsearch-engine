from contextlib import closing
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import main
from app.migrations import BASE_COLUMNS, MigrationError, migrate, require_current


class MigrationsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.sqlite3'

    def connect(self):
        return sqlite3.connect(self.path)

    def legacy(self, extra=''):
        with closing(self.connect()) as conn, conn:
            definition = ', '.join(f'{k} {v}' for k, v in BASE_COLUMNS.items())
            conn.execute(f'CREATE TABLE jobs ({definition}{extra})')
            for status in ('review', 'approved', 'rejected', 'draft_ready', 'tailoring', 'filtered'):
                conn.execute('INSERT INTO jobs(id,status,reason,description,score) VALUES(?,?,?,?,?)',
                             (status, status, 'original decision', 'original description', 83))

    def snapshot(self):
        with closing(self.connect()) as conn, conn:
            return conn.execute('SELECT * FROM jobs ORDER BY id').fetchall()

    def test_fresh_and_rerun(self):
        self.assertEqual(migrate(self.path), [1, 2])
        with closing(self.connect()) as conn, conn:
            require_current(conn)
            self.assertEqual(conn.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)
            before = conn.execute('SELECT * FROM schema_migrations').fetchall()
        self.assertEqual(migrate(self.path), [])
        with closing(self.connect()) as conn, conn:
            self.assertEqual(conn.execute('SELECT * FROM schema_migrations').fetchall(), before)

    def test_legacy_preserves_all_values(self):
        self.legacy()
        before = self.snapshot()
        migrate(self.path)
        self.assertEqual([r[:len(BASE_COLUMNS)] for r in self.snapshot()], before)
        self.assertTrue(all(r[-3:] == (None, None, None) for r in self.snapshot()))

    def test_classified_and_partial_legacy(self):
        for extra in (', classification TEXT', ', classification TEXT, classification_reason TEXT, classified_at TEXT, custom TEXT'):
            with self.subTest(extra=extra):
                self.path = Path(self.temp.name) / ('partial.sqlite3' if 'custom' not in extra else 'full.sqlite3')
                self.legacy(extra)
                with closing(self.connect()) as conn, conn:
                    conn.execute("UPDATE jobs SET classification='eligible' WHERE status='approved'")
                    conn.execute("UPDATE jobs SET classification='excluded' WHERE status='rejected'")
                before = self.snapshot()
                migrate(self.path)
                self.assertEqual([r[:len(before[0])] for r in self.snapshot()], before)
                self.assertEqual(migrate(self.path), [])

    def test_failure_rolls_back_ddl_and_history(self):
        self.legacy()
        before = self.snapshot()
        with patch('app.migrations.require_current', side_effect=MigrationError('injected failure')):
            with self.assertRaises(MigrationError):
                migrate(self.path)
        self.assertEqual(self.snapshot(), before)
        with closing(self.connect()) as conn, conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone())
        self.assertEqual(migrate(self.path), [1, 2])

    def test_unknown_history_rejected(self):
        migrate(self.path)
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT INTO schema_migrations(version,name) VALUES(99,'future')")
        before = self.snapshot()
        with self.assertRaises(MigrationError):
            migrate(self.path)
        self.assertEqual(self.snapshot(), before)

    def test_malformed_schema_rejected_unchanged(self):
        with closing(self.connect()) as conn, conn:
            conn.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY)')
            conn.execute("INSERT INTO jobs VALUES('preserve')")
        with self.assertRaises(MigrationError):
            migrate(self.path)
        self.assertEqual(self.snapshot(), [('preserve',)])

    def test_runtime_requires_explicit_migration(self):
        with patch.object(main, 'DB', self.path):
            with self.assertRaises(sqlite3.OperationalError):
                main.db()
            self.assertFalse(self.path.exists())
            self.legacy()
            with self.assertRaises(MigrationError):
                main.db()
            migrate(self.path)
            main.db().close()

    def test_concurrent_runners(self):
        self.legacy()
        before = self.snapshot()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(migrate, [self.path, self.path]))
        self.assertEqual(sorted(results), [[], [1, 2]])
        self.assertEqual([r[:len(BASE_COLUMNS)] for r in self.snapshot()], before)

    def test_upgrade_from_version_one(self):
        self.legacy()
        with closing(self.connect()) as conn, conn:
            conn.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)')
            conn.execute("INSERT INTO schema_migrations VALUES(1,'base_jobs','original timestamp')")
        self.assertEqual(migrate(self.path), [2])
        with closing(self.connect()) as conn, conn:
            self.assertEqual(conn.execute('SELECT applied_at FROM schema_migrations WHERE version=1').fetchone()[0], 'original timestamp')

    def test_cli_explicit_path_and_rerun(self):
        command = [sys.executable, '-m', 'app.migrations', '--database', str(self.path)]
        for expected in ('Applied versions: [1, 2]', 'Database already current'):
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(expected, result.stdout)
        result = subprocess.run([sys.executable, '-m', 'app.migrations'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
