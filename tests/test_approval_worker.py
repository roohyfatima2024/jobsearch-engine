from contextlib import closing
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app import approval_worker, main
from app.migrations import BASE_COLUMNS, MigrationError, migrate


class ApprovalWorkerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'worker.sqlite3'
        patcher = patch.object(main, 'DB', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        tailor = patch.object(approval_worker, 'tailor', side_effect=AssertionError('Must not tailor'))
        self.tailor = tailor.start()
        self.addCleanup(tailor.stop)

    def calls(self):
        return (approval_worker.claim, lambda: approval_worker.failed('job', 'error'))

    def test_missing_database_is_not_created(self):
        for call in self.calls():
            with self.subTest(call=call):
                with self.assertRaises(sqlite3.OperationalError):
                    call()
                self.assertFalse(self.path.exists())

    def test_unmigrated_database_is_unchanged(self):
        # Include classification so the old worker would have been able to write.
        with closing(sqlite3.connect(self.path)) as c, c:
            definition = ', '.join(f'{k} {v}' for k, v in BASE_COLUMNS.items())
            c.execute(f'CREATE TABLE jobs ({definition}, classification TEXT)')
            c.executemany('INSERT INTO jobs(id,status,classification) VALUES(?,?,?)',
                          [('job', 'tailoring', 'eligible'), ('approved', 'approved', 'eligible')])
        before = self.path.read_bytes()
        for call in self.calls():
            with self.subTest(call=call):
                with self.assertRaises(MigrationError):
                    call()
                self.assertEqual(self.path.read_bytes(), before)

    def seed(self):
        migrate(self.path)
        with closing(sqlite3.connect(self.path)) as c, c:
            c.executemany('INSERT INTO jobs(id,status,classification,score) VALUES(?,?,?,?)', [
                ('first', 'approved', 'eligible', 90),
                ('second', 'approved', 'eligible', 80),
                ('excluded', 'approved', 'excluded', 100),
                ('review', 'review', 'eligible', 100),
            ])

    def test_current_database_claim_and_failure(self):
        self.seed()
        statements = []
        def connection():
            c = main.db()
            self.assertEqual(c.execute('PRAGMA busy_timeout').fetchone()[0], 30000)
            c.set_trace_callback(statements.append)
            return c
        with patch.object(approval_worker, 'db', side_effect=connection):
            self.assertEqual(approval_worker.claim(), 'first')
        self.assertIn('BEGIN IMMEDIATE', statements)
        self.assertIn('COMMIT', statements)
        approval_worker.failed('first', 'x' * 400)
        approval_worker.failed('second', 'must not change approved job')
        with closing(sqlite3.connect(self.path)) as c, c:
            self.assertEqual(c.execute("SELECT status,reason FROM jobs WHERE id='first'").fetchone(),
                             ('tailor_failed', 'x' * 300))
            self.assertEqual(c.execute("SELECT status FROM jobs WHERE id='second'").fetchone(), ('approved',))
        self.assertEqual(approval_worker.claim(), 'second')
        self.assertIsNone(approval_worker.claim())
        self.tailor.assert_not_called()

    def test_concurrent_claims_are_distinct(self):
        self.seed()
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(lambda _: approval_worker.claim(), range(2)))
        self.assertEqual(set(claimed), {'first', 'second'})
        self.assertIsNone(approval_worker.claim())
        self.tailor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
