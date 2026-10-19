import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.migrations import BASE_COLUMNS, migrate
from dashboard import dashboard


class TrackedConnection(sqlite3.Connection):
    fail_after_write = False

    def execute(self, sql, parameters=()):
        result = super().execute(sql, parameters)
        if self.fail_after_write and sql.startswith('UPDATE jobs'):
            raise RuntimeError('injected after write')
        return result


class DashboardTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'jobs ?#.sqlite3'
        self.connections = []
        self.statements = []
        self.fail_after_write = False
        original_connect = sqlite3.connect

        def connect(*args, **kwargs):
            c = original_connect(*args, **kwargs, factory=TrackedConnection)
            c.fail_after_write = self.fail_after_write
            c.set_trace_callback(self.statements.append)
            self.connections.append(c)
            return c

        for p in (patch.object(dashboard, 'DB', self.path),
                  patch.object(dashboard, 'RESUMES', Path(temp.name) / 'resumes'),
                  patch.object(dashboard.sqlite3, 'connect', side_effect=connect)):
            p.start()
            self.addCleanup(p.stop)

    def request(self, path='/', body=None, headers=None, send_error=False):
        handler = object.__new__(dashboard.Handler)
        handler.path = path
        handler.headers = headers or {}
        handler.send = unittest.mock.Mock(side_effect=RuntimeError('send failed') if send_error else None)
        handler.redirect = unittest.mock.Mock()
        if body is None:
            handler.do_GET()
        else:
            handler.rfile = io.BytesIO(body.encode())
            handler.headers.setdefault('Content-Length', str(len(body)))
            handler.do_POST()
        for c in self.connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                c.execute('SELECT 1')
        return handler

    def seed(self):
        migrate(self.path)
        with closing(sqlite3.connect(self.path)) as c, c:
            c.executemany('INSERT INTO jobs(id,status,classification,score,title) VALUES(?,?,?,?,?)',
                          [(str(i), 'review', category, 90, 'Role') for i, category in
                           enumerate(('eligible', 'excluded', 'needs_review', 'low_match', None))])

    def status(self, job='0'):
        with closing(sqlite3.connect(self.path)) as c:
            return c.execute('SELECT status FROM jobs WHERE id=?', (job,)).fetchone()[0]

    def test_missing_database_never_created(self):
        for path, body in (('/', None), ('/decision', 'id=0&status=approved')):
            self.assertEqual(self.request(path, body).send.call_args.args[0], 500)
            self.assertFalse(self.path.exists())

    def test_invalid_schema_rejected_without_writes(self):
        for schema in ('legacy', 'future', 'malformed'):
            with self.subTest(schema=schema):
                self.path = self.path.parent / (schema + '.sqlite3')
                if schema == 'legacy':
                    with closing(sqlite3.connect(self.path)) as c, c:
                        definition = ', '.join(f'{k} {v}' for k, v in BASE_COLUMNS.items())
                        c.execute(f'CREATE TABLE jobs ({definition}, classification TEXT)')
                        c.execute("INSERT INTO jobs(id,status,classification) VALUES('0','review','eligible')")
                else:
                    self.seed()
                    with closing(sqlite3.connect(self.path)) as c, c:
                        if schema == 'future':
                            c.execute("INSERT INTO schema_migrations(version,name) VALUES(99,'future')")
                        else:
                            c.execute('ALTER TABLE jobs DROP COLUMN classification_reason')
                before = self.path.read_bytes()
                for path, body in (('/', None), ('/decision', 'id=0&status=approved')):
                    with patch.object(dashboard, 'DB', self.path):
                        self.assertEqual(self.request(path, body).send.call_args.args[0], 500)
                    self.assertEqual(self.path.read_bytes(), before)

    def test_get_routes_close_connections(self):
        self.seed()
        for path, code in (('/', 200), ('/job?id=0', 200), ('/job?id=missing', 404), ('/missing', 404)):
            with self.subTest(path=path):
                self.assertEqual(self.request(path).send.call_args.args[0], code)

    def test_response_exception_closes_connection(self):
        self.seed()
        with self.assertRaisesRegex(RuntimeError, 'send failed'):
            self.request(send_error=True)
        for c in self.connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                c.execute('SELECT 1')

    def test_only_eligible_can_be_approved(self):
        self.seed()
        for job in ('1', '2', '3', '4', 'missing'):
            handler = self.request('/decision', f'id={job}&status=approved')
            self.assertEqual(handler.send.call_args.args[0], 403)
            handler.redirect.assert_not_called()
            if job != 'missing':
                self.assertEqual(self.status(job), 'review')
        handler = self.request('/decision', 'id=0&status=approved')
        handler.redirect.assert_called_once()
        self.assertEqual(self.status(), 'approved')
        self.assertIn('COMMIT', self.statements)

    def test_other_decisions_and_security_checks(self):
        self.seed()
        for status in ('rejected', 'review'):
            self.request('/decision', f'id=1&status={status}').redirect.assert_called_once()
            self.assertEqual(self.status('1'), status)
        for path, body, headers, code in (
            ('/other', '', {}, 404),
            ('/decision', 'id=0&status=approved', {'Origin': 'http://evil.example'}, 403),
            ('/decision', '', {'Content-Length': '4097'}, 413),
            ('/decision', 'id=0&status=invalid', {}, 400),
        ):
            self.assertEqual(self.request(path, body, headers).send.call_args.args[0], code)
        self.assertEqual(self.status(), 'review')

    def test_failed_update_rolls_back_and_closes(self):
        self.seed()
        self.fail_after_write = True
        handler = self.request('/decision', 'id=0&status=approved')
        self.assertEqual(handler.send.call_args.args[0], 500)
        handler.redirect.assert_not_called()
        self.assertIn('ROLLBACK', self.statements)
        self.assertEqual(self.status(), 'review')

    def test_docker_layout_import_and_database_environment(self):
        # Reproduce the image COPY layout without building or starting Docker.
        root = Path(__file__).resolve().parents[1]
        image = self.path.parent / 'image'
        (image / 'app').mkdir(parents=True)
        shutil.copy(root / 'dashboard/dashboard.py', image / 'dashboard.py')
        for name in ('__init__.py', 'migrations.py'):
            shutil.copy(root / 'app' / name, image / 'app' / name)
        env = dict(os.environ, JOBS_DB='relative/jobs.sqlite3', PYTHONPATH='')
        result = subprocess.run([sys.executable, '-c',
                                 "import dashboard; assert str(dashboard.DB) == 'relative/jobs.sqlite3'; "
                                 "assert dashboard.require_current.__module__ == 'app.migrations'"],
                                cwd=image, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
