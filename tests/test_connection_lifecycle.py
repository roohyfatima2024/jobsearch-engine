import io
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app import approval_worker, main
from app.migrations import MigrationError, migrate


class TrackedConnection(sqlite3.Connection):
    fail_after_write = False

    def execute(self, sql, parameters=()):
        result = super().execute(sql, parameters)
        if self.fail_after_write and sql.lstrip().upper().startswith(('INSERT', 'UPDATE')):
            raise RuntimeError('injected after write')
        return result


class ConnectionLifecycleTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'jobs.sqlite3'
        migrate(self.path)
        self.connections = []
        self.statements = []
        original_connect = sqlite3.connect

        def connect(*args, **kwargs):
            c = original_connect(*args, **kwargs, factory=TrackedConnection)
            c.set_trace_callback(self.statements.append)
            self.connections.append(c)
            return c

        for patcher in (patch.object(main, 'DB', self.path),
                        patch.object(main.sqlite3, 'connect', side_effect=connect),
                        patch.object(main, 'config', return_value={'resume_generation_enabled': True}),
                        patch.object(main, 'ROOT', Path(temp.name))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_closed(self):
        self.assertTrue(self.connections)
        for c in self.connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                c.execute('SELECT 1')

    def seed(self):
        with closing(main.db()) as c, c:
            c.execute("INSERT INTO jobs(id,status,classification,score) VALUES('job','approved','eligible',90)")

    def test_worker_closes_on_claim_empty_claim_and_failure_update(self):
        self.seed()
        self.assertEqual(approval_worker.claim(), 'job')
        self.assertIsNone(approval_worker.claim())
        approval_worker.failed('job', 'error')
        self.assert_closed()
        self.assertIn('BEGIN IMMEDIATE', self.statements)
        with closing(main.db()) as c:
            self.assertEqual(c.execute("SELECT status FROM jobs").fetchone(), ('tailor_failed',))

    def test_worker_rolls_back_and_closes_on_exception(self):
        for operation in ('claim', 'failed'):
            with self.subTest(operation=operation):
                with closing(main.db()) as c, c:
                    c.execute('DELETE FROM jobs')
                    c.execute("INSERT INTO jobs(id,status,classification) VALUES('job',?,'eligible')",
                              ('approved' if operation == 'claim' else 'tailoring',))
                c = main.db()
                c.fail_after_write = True
                self.statements.clear()
                with patch.object(approval_worker, 'db', return_value=c):
                    with self.assertRaisesRegex(RuntimeError, 'injected'):
                        if operation == 'claim':
                            approval_worker.claim()
                        else:
                            approval_worker.failed('job', 'error')
                self.assert_closed()
                self.assertIn('ROLLBACK', self.statements)
                with closing(main.db()) as check:
                    self.assertEqual(check.execute('SELECT status FROM jobs').fetchone()[0],
                                     'approved' if operation == 'claim' else 'tailoring')

    def test_scan_and_list_close_on_success(self):
        with patch.object(main, 'fetch', return_value=[]), redirect_stdout(io.StringIO()):
            main.run()
            main.list_jobs()
        self.assert_closed()

    def test_scan_closes_and_rolls_back_pending_writes(self):
        job = dict(id='job', company='test', title='test', location='', url='', description='', salary='')
        c = main.db()
        c.fail_after_write = True
        with patch.object(main, 'db', return_value=c), patch.object(main, 'fetch', return_value=[job]), \
                patch.object(main, 'rank', return_value=(50, 'test')), \
                patch('app.job_classifier.classify_job', return_value=('eligible', [])):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                main.run()
        self.assert_closed()
        with closing(main.db()) as check:
            self.assertEqual(check.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)

    def test_scan_closes_on_fetch_failure(self):
        with patch.object(main, 'fetch', side_effect=RuntimeError('fetch failed')):
            with self.assertRaises(RuntimeError):
                main.run()
        self.assert_closed()

    def test_list_closes_on_output_failure(self):
        with patch('builtins.print', side_effect=RuntimeError('output failed')):
            with self.assertRaises(RuntimeError):
                main.list_jobs()
        self.assert_closed()

    def test_tailor_closes_on_system_exit_and_profile_failure(self):
        with self.assertRaises(SystemExit):
            main.tailor('missing')
        self.seed()
        with self.assertRaises(FileNotFoundError):
            main.tailor('job')
        self.assert_closed()

    def test_tailor_success_commits_and_closes(self):
        self.seed()
        with closing(main.db()) as c, c:
            c.execute("UPDATE jobs SET company='Test', title='Role', description='Description', url=''")
        profile = self.path.parent / 'config' / 'verified_profile.txt'
        profile.parent.mkdir()
        profile.write_text('Test profile')
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'test-key'}), \
                patch.object(main.urllib.request, 'urlopen',
                             return_value=io.BytesIO(b'{"content":[{"type":"text","text":"Test draft"}]}')), \
                redirect_stdout(io.StringIO()):
            main.tailor('job')
        self.assert_closed()
        self.assertEqual((self.path.parent / 'resumes' / 'draft_job.txt').read_text(), 'Test draft')
        with closing(main.db()) as c:
            self.assertEqual(c.execute('SELECT status FROM jobs').fetchone(), ('draft_ready',))

    def test_schema_validation_failure_closes(self):
        with patch('app.migrations.require_current', side_effect=MigrationError('invalid')):
            with self.assertRaises(MigrationError):
                main.db()
        self.assert_closed()


if __name__ == '__main__':
    unittest.main()
