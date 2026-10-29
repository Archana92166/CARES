"""Small SQLite persistence layer for the CARES application backend."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Sequence


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS guardian_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    phone_number TEXT NOT NULL,
    relationship TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitoring_sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    scenario TEXT,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    stopped_at TEXT,
    metadata TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS engine_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    timestamp REAL NOT NULL,
    heart_rate REAL NOT NULL,
    baseline REAL NOT NULL,
    deviation REAL NOT NULL,
    percentage_deviation REAL NOT NULL,
    risk_level TEXT NOT NULL,
    risk_score REAL NOT NULL,
    confidence REAL NOT NULL,
    trend REAL NOT NULL,
    persistence REAL NOT NULL,
    recovery_state TEXT NOT NULL,
    reason_codes TEXT NOT NULL,
    explanation TEXT NOT NULL,
    recommended_actions TEXT NOT NULL,
    created_at TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT 'legacy',
    source TEXT NOT NULL DEFAULT 'REAL_HARDWARE'
);

CREATE TABLE IF NOT EXISTS guardian_action_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    engine_event_id INTEGER NOT NULL REFERENCES engine_events(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    status TEXT NOT NULL,
    timestamp REAL NOT NULL,
    metadata TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT 'legacy',
    source TEXT NOT NULL DEFAULT 'REAL_HARDWARE'
);

CREATE TABLE IF NOT EXISTS location_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    timestamp REAL NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    accuracy REAL,
    source TEXT NOT NULL,
    formatted_address TEXT NOT NULL,
    provider TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT 'legacy'
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    engine_event_id INTEGER NOT NULL REFERENCES engine_events(id) ON DELETE CASCADE,
    risk_level TEXT NOT NULL,
    timestamp REAL NOT NULL,
    explanation TEXT NOT NULL,
    location_event_id INTEGER REFERENCES location_events(id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT 'legacy',
    source TEXT NOT NULL DEFAULT 'REAL_HARDWARE'
);

CREATE TABLE IF NOT EXISTS baseline_daily_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    trusted_samples INTEGER NOT NULL,
    mean_bpm REAL NOT NULL,
    median_bpm REAL NOT NULL,
    std_bpm REAL NOT NULL,
    minimum_bpm REAL NOT NULL,
    maximum_bpm REAL NOT NULL,
    eligible_observations INTEGER NOT NULL,
    adaptation_updates INTEGER NOT NULL,
    adaptation_holds INTEGER NOT NULL,
    session_id TEXT NOT NULL DEFAULT 'legacy',
    source TEXT NOT NULL DEFAULT 'REAL_HARDWARE',
    UNIQUE(user_id, date)
);

CREATE TABLE IF NOT EXISTS baseline_adaptation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    previous_baseline REAL,
    observation_mean REAL,
    observation_std REAL,
    deviation REAL,
    risk_level TEXT NOT NULL,
    valid_samples INTEGER NOT NULL,
    required_samples INTEGER NOT NULL,
    signal_quality REAL,
    decision TEXT NOT NULL,
    new_baseline REAL,
    reason TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT 'legacy',
    source TEXT NOT NULL DEFAULT 'REAL_HARDWARE'
);

CREATE INDEX IF NOT EXISTS idx_engine_events_user_time
    ON engine_events(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_actions_user_time
    ON guardian_action_events(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_locations_user_time
    ON location_events(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_incidents_user_time
    ON incidents(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_adaptation_user_time
    ON baseline_adaptation_events(user_id, id DESC);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _PostgresCursor:
    """Compatibility for existing code that reads cursor.lastrowid."""

    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _PostgresTransactionConnection:
    """Adapt legacy transaction SQL to PostgreSQL."""

    def __init__(self, database):
        self.database = database

    def execute(self, sql, parameters=()):
        return self.database._execute_postgres(sql, parameters)


class Database:
    """Thread-safe SQLite/PostgreSQL persistence layer."""

    ID_TABLES = {
        "users",
        "sessions",
        "guardian_contacts",
        "engine_events",
        "guardian_action_events",
        "location_events",
        "incidents",
        "baseline_daily_records",
        "baseline_adaptation_events",
    }

    def __init__(self, path="data/cares.sqlite3", database_url=None):
        import os

        self._lock = threading.RLock()
        self.database_url = (
            database_url or os.getenv("DATABASE_URL") or ""
        ).strip()
        self.is_postgres = bool(self.database_url)

        if self.is_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError(
                    "DATABASE_URL is set, but psycopg is missing. "
                    "Install psycopg[binary]."
                ) from exc

            if self.database_url.startswith("postgres://"):
                self.database_url = (
                    "postgresql://"
                    + self.database_url[len("postgres://"):]
                )

            self.path = None
            self.connection = psycopg.connect(
                self.database_url,
                autocommit=True,
                row_factory=dict_row,
                connect_timeout=10,
            )
            self._initialize_postgres()

        else:
            self.path = Path(path)

            if str(self.path) != ":memory:":
                self.path.parent.mkdir(
                    parents=True, exist_ok=True
                )

            self.connection = sqlite3.connect(
                str(self.path),
                check_same_thread=False,
                isolation_level=None,
            )
            self.connection.row_factory = sqlite3.Row

            with self._lock:
                self.connection.execute(
                    "PRAGMA foreign_keys = ON"
                )
                self.connection.execute(
                    "PRAGMA journal_mode = WAL"
                )
                self.connection.executescript(SCHEMA)
                self._migrate_legacy_columns()
                self.connection.execute(
                    "CREATE INDEX IF NOT EXISTS "
                    "idx_engine_events_session_time "
                    "ON engine_events(user_id, session_id, id DESC)"
                )

    def _initialize_postgres(self):
        import re

        schema = re.sub(
            r"(?im)^\s*PRAGMA\s+[^;]+;\s*", "", SCHEMA
        )
        schema = re.sub(
            r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
            "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
            schema,
            flags=re.IGNORECASE,
        )
        schema = re.sub(
            r"(email\s+TEXT\s+NOT\s+NULL\s+UNIQUE)"
            r"\s+COLLATE\s+NOCASE",
            r"\1",
            schema,
            flags=re.IGNORECASE,
        )

        with self._lock:
            for statement in schema.split(";"):
                statement = statement.strip()
                if statement:
                    self.connection.execute(statement)

            self._migrate_legacy_columns()
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_engine_events_session_time "
                "ON engine_events(user_id, session_id, id DESC)"
            )

    def _migrate_legacy_columns(self):
        migrations = {
            "engine_events": {
                "session_id": "TEXT NOT NULL DEFAULT 'legacy'",
                "source": "TEXT NOT NULL DEFAULT 'REAL_HARDWARE'",
            },
            "guardian_action_events": {
                "session_id": "TEXT NOT NULL DEFAULT 'legacy'",
                "source": "TEXT NOT NULL DEFAULT 'REAL_HARDWARE'",
            },
            "location_events": {
                "session_id": "TEXT NOT NULL DEFAULT 'legacy'",
            },
            "incidents": {
                "session_id": "TEXT NOT NULL DEFAULT 'legacy'",
                "source": "TEXT NOT NULL DEFAULT 'REAL_HARDWARE'",
            },
            "baseline_daily_records": {
                "session_id": "TEXT NOT NULL DEFAULT 'legacy'",
                "source": "TEXT NOT NULL DEFAULT 'REAL_HARDWARE'",
            },
            "baseline_adaptation_events": {
                "session_id": "TEXT NOT NULL DEFAULT 'legacy'",
                "source": "TEXT NOT NULL DEFAULT 'REAL_HARDWARE'",
            },
        }

        for table, columns in migrations.items():
            if self.is_postgres:
                rows = self.connection.execute(
                    "SELECT column_name AS name "
                    "FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = %s",
                    (table,),
                ).fetchall()
                existing = {row["name"] for row in rows}
            else:
                rows = self.connection.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
                existing = {row["name"] for row in rows}

            for column, declaration in columns.items():
                if column not in existing:
                    self.connection.execute(
                        f"ALTER TABLE {table} "
                        f"ADD COLUMN {column} {declaration}"
                    )

    def _execute_postgres(self, sql, parameters=()):
        import re

        with self._lock:
            pg_sql = sql.replace("?", "%s")

            match = re.match(
                r"\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)",
                pg_sql,
                flags=re.IGNORECASE,
            )
            table = match.group(1).lower() if match else None

            returning_id = (
                table in self.ID_TABLES
                and " RETURNING " not in pg_sql.upper()
            )

            if returning_id:
                pg_sql = pg_sql.rstrip().rstrip(";")
                pg_sql += " RETURNING id"

            cursor = self.connection.execute(
                pg_sql, tuple(parameters)
            )

            generated_id = None
            if returning_id:
                row = cursor.fetchone()
                if row is not None:
                    generated_id = int(row["id"])

            return _PostgresCursor(cursor, generated_id)

    @contextmanager
    def transaction(self):
        with self._lock:
            if self.is_postgres:
                self.connection.execute("BEGIN")
                transaction_connection = (
                    _PostgresTransactionConnection(self)
                )

                try:
                    yield transaction_connection
                except Exception:
                    self.connection.execute("ROLLBACK")
                    raise
                else:
                    self.connection.execute("COMMIT")

            else:
                self.connection.execute("BEGIN")
                try:
                    yield self.connection
                except Exception:
                    self.connection.rollback()
                    raise
                else:
                    self.connection.commit()

    def execute(self, sql, parameters=()):
        with self._lock:
            if self.is_postgres:
                return self._execute_postgres(sql, parameters)

            return self.connection.execute(sql, parameters)

    def fetch_one(self, sql, parameters=()):
        with self._lock:
            if self.is_postgres:
                return self.connection.execute(
                    sql.replace("?", "%s"),
                    tuple(parameters),
                ).fetchone()

            return self.connection.execute(
                sql, parameters
            ).fetchone()

    def fetch_all(self, sql, parameters=()):
        with self._lock:
            if self.is_postgres:
                return self.connection.execute(
                    sql.replace("?", "%s"),
                    tuple(parameters),
                ).fetchall()

            return self.connection.execute(
                sql, parameters
            ).fetchall()

    def close(self):
        with self._lock:
            self.connection.close()

