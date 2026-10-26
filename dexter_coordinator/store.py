"""SQLite storage: one local file, no server, no paid infrastructure.

Every change runs in one transaction and appends to the activity history. Each
history entry carries a hash of the entry before it, so `check` can tell when
entries were edited or removed from the middle of the file. That is tamper
evidence against accidents and casual edits, not security: someone who can
write the file can rewrite the whole chain.
"""

import contextlib
import hashlib
import json
import os
import sqlite3

from .models import DexterError

SCHEMA_VERSION = 1
GENESIS_HASH = "0" * 64

SCHEMA = """
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE owner (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    salt TEXT NOT NULL,
    hash TEXT NOT NULL,
    iterations INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE projects (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'PAUSED')),
    pause_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    priority TEXT NOT NULL,
    assignee TEXT,
    criteria TEXT NOT NULL,
    state TEXT NOT NULL,
    state_seq INTEGER NOT NULL,
    blocked_from TEXT,
    blocked_reason TEXT,
    approval TEXT NOT NULL,
    acceptance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE dependencies (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    depends_on INTEGER NOT NULL REFERENCES tasks(id),
    PRIMARY KEY (task_id, depends_on),
    CHECK (task_id != depends_on)
);
CREATE TABLE handoffs (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE,
    correlation_id TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    from_role TEXT NOT NULL,
    to_role TEXT NOT NULL,
    action TEXT NOT NULL,
    payload TEXT NOT NULL,
    message TEXT NOT NULL,
    delivery TEXT NOT NULL,
    created_at TEXT NOT NULL,
    marked_sent_at TEXT
);
CREATE TABLE responses (
    id INTEGER PRIMARY KEY,
    handoff_id INTEGER NOT NULL UNIQUE REFERENCES handoffs(id),
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    from_role TEXT NOT NULL,
    structured INTEGER NOT NULL,
    outcome TEXT,
    data TEXT NOT NULL,
    raw TEXT NOT NULL,
    flags TEXT NOT NULL,
    seq INTEGER NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE events (
    seq INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    project_id INTEGER,
    task_id INTEGER,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
"""


class StoreError(DexterError):
    """The database file is missing, unreadable, or from an unknown version."""


def dumps(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_hash(prev_hash, seq, ts, project_id, task_id, actor, action, details_json):
    body = dumps([seq, ts, project_id, task_id, actor, action, details_json])
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path):
        self.path = os.path.abspath(os.path.expanduser(path))
        directory = os.path.dirname(self.path)
        try:
            if not os.path.isdir(directory):
                os.makedirs(directory, mode=0o700, exist_ok=True)
            new = not os.path.exists(self.path)
            self.conn = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        except (OSError, sqlite3.Error) as exc:
            raise StoreError(f"cannot open the database at {self.path}: {exc}") from None
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()
        if new:
            with contextlib.suppress(OSError):
                os.chmod(self.path, 0o600)

    def close(self):
        self.conn.close()

    def _migrate(self):
        try:
            tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        except sqlite3.DatabaseError as exc:
            raise StoreError(f"{self.path} is not a readable Dexter database: {exc}") from None
        if not tables:
            with self.transaction():
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        self.conn.execute(statement)
                self.conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            return
        if "meta" not in tables:
            raise StoreError(f"{self.path} is not a Dexter database")
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None or row[0] != str(SCHEMA_VERSION):
            raise StoreError(f"{self.path} uses an unknown database version; use the matching Dexter version")

    @contextlib.contextmanager
    def transaction(self):
        """One atomic change. BEGIN IMMEDIATE also stops two processes interleaving writes."""
        if self.conn.in_transaction:
            yield self.conn
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def one(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()

    def all(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def add_event(self, ts, actor, action, details, project_id=None, task_id=None):
        """Append to the activity history. Must be called inside a transaction."""
        assert self.conn.in_transaction, "events are only written inside a transaction"
        last = self.one("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1")
        seq = (last["seq"] + 1) if last else 1
        prev = last["hash"] if last else GENESIS_HASH
        details_json = dumps(details)
        digest = event_hash(prev, seq, ts, project_id, task_id, actor, action, details_json)
        self.conn.execute(
            "INSERT INTO events (seq, ts, project_id, task_id, actor, action, details, prev_hash, hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (seq, ts, project_id, task_id, actor, action, details_json, prev, digest),
        )
        return seq

    def verify_events(self):
        """Return a list of problems with the history chain (empty if it is intact)."""
        problems = []
        prev, expected_seq = GENESIS_HASH, 1
        for row in self.all("SELECT * FROM events ORDER BY seq"):
            if row["seq"] != expected_seq:
                problems.append(f"history entry {expected_seq} is missing")
            if row["prev_hash"] != prev:
                problems.append(f"history entry {row['seq']} does not follow the entry before it")
            digest = event_hash(
                row["prev_hash"], row["seq"], row["ts"], row["project_id"], row["task_id"],
                row["actor"], row["action"], row["details"],
            )
            if digest != row["hash"]:
                problems.append(f"history entry {row['seq']} was changed after it was written")
            prev, expected_seq = row["hash"], row["seq"] + 1
        return problems

    def integrity_problems(self):
        rows = self.all("PRAGMA integrity_check")
        result = [r[0] for r in rows]
        return [] if result == ["ok"] else result

    def backup(self, destination):
        destination = os.path.abspath(os.path.expanduser(destination))
        if os.path.exists(destination):
            raise StoreError(f"{destination} already exists; choose a new file name")
        target = sqlite3.connect(destination)
        try:
            self.conn.backup(target)
        finally:
            target.close()
        with contextlib.suppress(OSError):
            os.chmod(destination, 0o600)
        return destination
