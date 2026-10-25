"""SQLite persistence; every mutation and its audit record share a transaction."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False)


SCHEMA = """
CREATE TABLE IF NOT EXISTS drugs (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, manufacturer TEXT NOT NULL DEFAULT '',
 specification TEXT NOT NULL DEFAULT '', approval_number TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
 id INTEGER PRIMARY KEY, drug_id INTEGER NOT NULL REFERENCES drugs(id),
 label TEXT NOT NULL, version_date TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
 filename TEXT NOT NULL, sha256 TEXT NOT NULL, storage_name TEXT NOT NULL,
 text TEXT NOT NULL, sections TEXT NOT NULL, warnings TEXT NOT NULL DEFAULT '[]',
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comparisons (
 id INTEGER PRIMARY KEY, drug_id INTEGER NOT NULL REFERENCES drugs(id),
 old_version_id INTEGER NOT NULL REFERENCES versions(id),
 new_version_id INTEGER NOT NULL REFERENCES versions(id),
 unchanged_sections INTEGER NOT NULL, warnings TEXT NOT NULL DEFAULT '[]',
 created_at TEXT NOT NULL,
 CHECK(old_version_id != new_version_id)
);
CREATE TABLE IF NOT EXISTS changes (
 id INTEGER PRIMARY KEY, comparison_id INTEGER NOT NULL REFERENCES comparisons(id),
 payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending'
 CHECK(status IN ('pending','confirmed','ignored')),
 note TEXT NOT NULL DEFAULT '', reviewer TEXT NOT NULL DEFAULT '',
 reviewed_at TEXT, revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, action TEXT NOT NULL, entity_type TEXT NOT NULL,
 entity_id INTEGER NOT NULL, detail TEXT NOT NULL, actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS versions_drug ON versions(drug_id);
CREATE INDEX IF NOT EXISTS changes_comparison ON changes(comparison_id);
CREATE INDEX IF NOT EXISTS comparisons_versions ON comparisons(old_version_id,new_version_id);
"""


class Database:
    def __init__(self, data_dir):
        self.directory = Path(data_dir).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.uploads = self.directory / "uploads"
        self.uploads.mkdir(exist_ok=True)
        self.path = self.directory / "medlabel.sqlite3"
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self, write=False):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def audit(connection, actor, action, entity_type, entity_id, detail):
    connection.execute(
        "INSERT INTO audit(action,entity_type,entity_id,detail,actor,created_at) VALUES(?,?,?,?,?,?)",
        (action, entity_type, entity_id, encode(detail) if not isinstance(detail, str) else detail, actor, now()),
    )
