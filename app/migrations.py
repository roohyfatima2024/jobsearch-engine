"""Explicit, additive SQLite migrations. Never import this module to migrate."""
import argparse
import sqlite3
from pathlib import Path

BASE_COLUMNS = {
    'id': 'TEXT PRIMARY KEY', 'company': 'TEXT', 'title': 'TEXT',
    'location': 'TEXT', 'url': 'TEXT', 'description': 'TEXT', 'salary': 'TEXT',
    'score': 'INTEGER DEFAULT 0', "status": "TEXT DEFAULT 'discovered'",
    'reason': "TEXT DEFAULT ''", 'discovered_at': 'TEXT',
}
CLASS_COLUMNS = ('classification', 'classification_reason', 'classified_at')
VERSIONS = ((1, 'base_jobs'), (2, 'classification_columns'))


class MigrationError(RuntimeError):
    pass


def columns(conn):
    return {row[1]: row for row in conn.execute('PRAGMA table_info(jobs)')}


def validate_base(conn):
    info = columns(conn)
    missing = BASE_COLUMNS.keys() - info.keys()
    if missing or not info.get('id', (None,) * 6)[5]:
        raise MigrationError(f'Unsupported jobs schema (missing columns: {sorted(missing)}; id must be primary key)')


def history(conn):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    rows = conn.execute('SELECT version, name FROM schema_migrations ORDER BY version').fetchall() if exists else []
    if rows != list(VERSIONS[:len(rows)]):
        raise MigrationError('Unknown or inconsistent migration history; use the matching application version')
    return rows


def require_current(conn):
    if history(conn) != list(VERSIONS):
        raise MigrationError('Database requires explicit migration: python -m app.migrations --database PATH')
    validate_base(conn)
    if not set(CLASS_COLUMNS) <= columns(conn).keys():
        raise MigrationError('Migration history does not match jobs schema')


def migrate(database):
    """Apply all pending versions atomically, serializing concurrent runners."""
    path = Path(database).expanduser().resolve()
    if not path.parent.is_dir():
        raise MigrationError('Database parent directory must already exist')
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        conn.execute('BEGIN IMMEDIATE')
        applied = history(conn)
        conn.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )''')
        pending = VERSIONS[len(applied):]
        for version, name in pending:
            if version == 1:
                definition = ', '.join(f'{key} {value}' for key, value in BASE_COLUMNS.items())
                conn.execute(f'CREATE TABLE IF NOT EXISTS jobs ({definition})')
                validate_base(conn)
            elif version == 2:
                validate_base(conn)
                for column in CLASS_COLUMNS:
                    if column not in columns(conn):
                        conn.execute(f'ALTER TABLE jobs ADD COLUMN {column} TEXT')
            conn.execute('INSERT INTO schema_migrations(version, name) VALUES (?, ?)', (version, name))
        require_current(conn)
        conn.execute('COMMIT')
        return [version for version, _ in pending]
    except BaseException:
        if conn.in_transaction:
            conn.execute('ROLLBACK')
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path,
                        help='Explicit database path; no production default')
    args = parser.parse_args()
    try:
        versions = migrate(args.database)
    except (sqlite3.Error, MigrationError) as exc:
        parser.exit(1, f'Migration failed: {exc}\n')
    print(f'Applied versions: {versions}' if versions else 'Database already current')


if __name__ == '__main__':
    main()
