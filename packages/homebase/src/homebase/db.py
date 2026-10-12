"""SQLite persistence for HomeBase: Machines, Projects, Sessions, stored files and the Handoff log."""

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS machines (
    name TEXT PRIMARY KEY,
    node_id TEXT UNIQUE NOT NULL,
    workspace_root TEXT NOT NULL,
    harness_versions TEXT NOT NULL DEFAULT '{}',
    registered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    path TEXT PRIMARY KEY,
    remote TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    handle TEXT UNIQUE NOT NULL,
    harness TEXT NOT NULL,
    harness_version TEXT,
    project_path TEXT NOT NULL REFERENCES projects(path),
    branch TEXT NOT NULL,
    subdir TEXT NOT NULL DEFAULT '',
    title TEXT,
    holder TEXT NOT NULL REFERENCES machines(name),
    is_open INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    tombstoned_at TEXT
);
CREATE TABLE IF NOT EXISTS session_files (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    root TEXT NOT NULL,
    PRIMARY KEY (session_id, path)
);
CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    from_machine TEXT NOT NULL,
    to_machine TEXT NOT NULL,
    at TEXT NOT NULL,
    forced INTEGER NOT NULL,
    initiator_kind TEXT NOT NULL,
    initiator_session TEXT
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()

    def query(self, sql: str, *args) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, *args) -> dict | None:
        rows = self.query(sql, *args)
        return rows[0] if rows else None

    def execute(self, sql: str, *args) -> None:
        with self.lock, self.conn:
            self.conn.execute(sql, args)

    # Machines

    def machine(self, name: str) -> dict | None:
        row = self.one("SELECT * FROM machines WHERE name = ?", name)
        if row:
            row["harness_versions"] = json.loads(row["harness_versions"])
        return row

    def machine_by_node(self, node_id: str) -> dict | None:
        row = self.one("SELECT name FROM machines WHERE node_id = ?", node_id)
        return self.machine(row["name"]) if row else None

    def machines(self) -> list[dict]:
        return [self.machine(r["name"]) for r in self.query("SELECT name FROM machines ORDER BY name")]
