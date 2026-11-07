import logging
import sqlite3
from collections.abc import Sequence
from contextlib import contextmanager

from app.config import DATABASE_PATH

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password       TEXT NOT NULL,
    display_name   TEXT,
    role           TEXT NOT NULL DEFAULT 'viewer' CHECK(role IN ('admin','editor','viewer')),
    token_version  INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS user_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id),
    action     TEXT NOT NULL CHECK(action IN ('create', 'update', 'delete')),
    details    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_user_log_user_id ON user_log(user_id);
CREATE INDEX IF NOT EXISTS idx_user_log_created_at ON user_log(created_at);

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# Versioned migrations: (version, description, sql)
# Append new migrations to the end. Never modify or reorder existing entries.
# Each migration can be a single SQL statement or a multi-statement script.
# Each migration is applied in its own transaction, so if it fails the database is left unchanged.
# If you create a table you must also add a corresponding entry to MIGRATION_TABLES
# and add the indexes there as migrations can only be one transaction at a time
# and the indexes must be created in a separate transaction.
MIGRATIONS: Sequence[tuple[int, str, str]] = (
    (1, "Add log_entries table", """
        CREATE TABLE IF NOT EXISTS log_entries (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT NOT NULL DEFAULT (datetime('now')),
            level      TEXT NOT NULL,
            module     TEXT,
            message    TEXT NOT NULL
        );
    """),
    (2, "Add settings table", """
        CREATE TABLE IF NOT EXISTS settings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            key        TEXT NOT NULL UNIQUE,
            value      TEXT
        );
    """),
    (3, "Add data_theme column to users table", """
        ALTER TABLE users ADD COLUMN data_theme TEXT DEFAULT 'dim';
    """)

)

MIGRATION_TABLES = """
CREATE TABLE IF NOT EXISTS log_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT NOT NULL DEFAULT (datetime('now')),
    level      TEXT NOT NULL,
    module     TEXT,
    message    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_entries_timestamp ON log_entries(timestamp);
CREATE INDEX IF NOT EXISTS idx_log_entries_level ON log_entries(level);

CREATE TABLE IF NOT EXISTS settings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL UNIQUE,
    value      TEXT
);
CREATE INDEX IF NOT EXISTS idx_settings_key ON settings(key);
INSERT OR IGNORE INTO settings (key, value) VALUES ('app_name', 'pyexpense-tracker');
INSERT OR IGNORE INTO settings (key, value) VALUES ('app_version', '0.1.0');
INSERT OR IGNORE INTO settings (key, value) VALUES ('data_theme', 'dim');

"""


@contextmanager
def get_db():
    """Yields a SQLite connection that auto-commits on success, rolls back on error."""
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initializes the database with the schema."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as db:
        db.executescript(SCHEMA)
        migration_logs = _run_migrations(db)
    # Only now, with the migration transaction committed and its connection
    # closed, is it safe for SQLiteHandler to open its own connection and
    # write these records to log_entries.
    for line in migration_logs:
        logger.info("%s", line)


def _run_migrations(db: sqlite3.Connection) -> list[str]:
    """Apply pending migrations. Returns log lines for the caller to emit.

    Nothing here logs directly, and callers must emit the returned lines only
    after this connection's transaction has committed. SQLiteHandler writes
    every log record to the log_entries table on a *second* connection to this
    same database, so a log call from inside the migration write transaction
    waits out SQLite's full busy timeout and then fails — five pending
    migrations meant ~25s of startup and five tracebacks that looked, to
    anyone upgrading, exactly like a failed migration.
    """
    logs: list[str] = []
    applied = {
        r["version"]
        for r in db.execute("SELECT version FROM schema_version").fetchall()
    }

    for version, description, sql in MIGRATIONS:
        if version in applied:
            continue
        # One transaction per migration, so the schema change and the row
        # that records it commit together or not at all.
        #
        # Without this, a migration's ALTER could land while its
        # schema_version row did not, wedging the database permanently
        # (issue #24). The mechanism is narrower than it looks: under
        # sqlite3's default (legacy) transaction control an implicit
        # transaction is opened before DML only, never before DDL. So an
        # ALTER issued while no transaction was open ran in autocommit and
        # landed alone, while its INSERT opened a transaction that stayed
        # pending until the executescript below — which is why only the
        # *first* pending migration wedged and every later one in the same
        # run rolled back cleanly.
        db.execute("BEGIN IMMEDIATE")
        # Re-read under the write lock. `applied` was sampled before the
        # loop, so it is stale if another runner (a concurrent boot, or a
        # restore migrating the live database) committed this version
        # while we waited for the lock.
        if db.execute(
            "SELECT 1 FROM schema_version WHERE version = ?", (version,)
        ).fetchone():
            db.commit()
            continue
        try:
            # Apply the migration
            db.execute(sql)
        except sqlite3.OperationalError as e:
            # raise should cause the transaction to roll back in the context manager
            raise
        # Record the migration in the schema_version table
        db.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
        db.commit()
        logs.append(f"Applied migration {version}: {description}")

    db.executescript(MIGRATION_TABLES)
    return logs
