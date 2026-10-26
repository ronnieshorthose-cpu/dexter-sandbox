"""SQLite storage: one local file, no server, no paid infrastructure.

Every change runs in one transaction and appends to the activity history.

Tamper evidence, and its limits:
  - Each history entry carries a SHA-256 hash of the entry before it. This
    catches edits to, or removal of, entries in the middle of the history, but
    anyone who can write the file can recompute the whole chain.
  - Entries made by the owner also carry an HMAC signature with the owner's
    key (see auth.py). Forging or editing those needs the passphrase. Someone
    who has the file *and* the passphrase can forge anything.
  - Nothing here detects the whole file being replaced by an older copy. Keep
    the history anchor that `check --owner` prints somewhere else to spot that.

The file's structure is checked on every open: extra tables, triggers, views or
changed table definitions are refused, so a crafted file cannot run its own SQL
when Dexter writes to it.
"""

import contextlib
import hashlib
import json
import os
import sqlite3

from .auth import sign, signature_matches
from .models import HUMAN, DexterError

SCHEMA_VERSION = 2
GENESIS_HASH = "0" * 64
BUSY_TIMEOUT_SECONDS = 10

SCHEMA = """
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE owner (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    kdf TEXT NOT NULL,
    salt TEXT NOT NULL,
    cost INTEGER NOT NULL,
    verifier TEXT NOT NULL,
    wrapped_key TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0,
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
    created_seq INTEGER NOT NULL,
    task_state_seq INTEGER NOT NULL,
    task_content_hash TEXT NOT NULL,
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
    hash TEXT NOT NULL,
    owner_mac TEXT
);
"""
STATEMENTS = tuple(s.strip() for s in SCHEMA.split(";") if s.strip())


class StoreError(DexterError):
    """The database file is missing, unreadable, unsafe, or not a Dexter database."""


def dumps(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(value):
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def event_hash(prev_hash, seq, ts, project_id, task_id, actor, action, details_json, owner_mac=None):
    body = dumps([seq, ts, project_id, task_id, actor, action, details_json, owner_mac])
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def signed_fields(prev_hash, seq, ts, project_id, task_id, actor, action, details_json):
    """What an owner signature covers: the entry and its link to the entry before it."""
    return ["dexter-event-v1", prev_hash, seq, ts, project_id, task_id, actor, action, details_json]


def inside_git_work_tree(path):
    """True if path, or any folder above it, contains a .git entry."""
    current = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def _normalize_sql(text):
    return " ".join(text.split()) if text else None


def _expected_objects():
    memory = sqlite3.connect(":memory:")
    try:
        for statement in STATEMENTS:
            memory.execute(statement)
        return {(r[0], r[1], r[2], _normalize_sql(r[3]))
                for r in memory.execute("SELECT type, name, tbl_name, sql FROM sqlite_master")}
    finally:
        memory.close()


def _create_private_file(path):
    """Create an empty file readable only by its owner; never follow or replace an existing path."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    os.close(fd)


class Store:
    def __init__(self, path, *, allow_inside_git=False):
        self.path = os.path.abspath(os.path.expanduser(path))
        directory = os.path.dirname(self.path)
        new = not os.path.lexists(self.path)
        if new:
            if not allow_inside_git and inside_git_work_tree(self.path):
                raise StoreError(
                    f"refusing to create a database inside a git working tree ({self.path}), where it could be "
                    "committed by accident. Choose a path outside the repository, such as ~/.dexter/coordinator.db"
                )
            try:
                if not os.path.isdir(directory):
                    os.makedirs(directory, mode=0o700, exist_ok=True)
                _create_private_file(self.path)
            except OSError as exc:
                raise StoreError(f"cannot create the database at {self.path}: {exc.strerror}") from None
        try:
            self.conn = sqlite3.connect(self.path, isolation_level=None, timeout=BUSY_TIMEOUT_SECONDS)
        except sqlite3.Error as exc:
            raise StoreError(f"cannot open the database at {self.path}: {exc}") from None
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA foreign_keys = ON")
            with contextlib.suppress(sqlite3.Error):
                self.conn.execute("PRAGMA trusted_schema = OFF")
            self._open_or_create()
        except sqlite3.DatabaseError as exc:
            self.conn.close()
            raise StoreError(f"{self.path} is not a readable Dexter database: {exc}") from None
        except StoreError:
            self.conn.close()
            raise

    def close(self):
        self.conn.close()

    def _open_or_create(self):
        objects = self.conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall()
        if not objects:
            with self.transaction():
                for statement in STATEMENTS:
                    self.conn.execute(statement)
                self.conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            return
        names = {r[1] for r in objects}
        if "meta" not in names:
            raise StoreError(f"{self.path} is not a Dexter database")
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None or row[0] != str(SCHEMA_VERSION):
            found = row[0] if row else "unknown"
            raise StoreError(
                f"{self.path} has database version {found}; this Dexter needs version {SCHEMA_VERSION}. "
                "Databases from the first Phase 3A test version cannot be upgraded: start a new one"
            )
        actual = {(r[0], r[1], r[2], _normalize_sql(r[3])) for r in objects}
        expected = _expected_objects()
        changed = sorted({name for _, name, _, _ in actual ^ expected})
        if changed:
            detail = ", ".join(json.dumps(n[:40]) for n in changed[:5])
            raise StoreError(
                f"{self.path} does not have the expected Dexter structure (unexpected or changed: {detail}). "
                "Extra tables, triggers or views are refused, because they could change records behind Dexter's back"
            )

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

    # -- history -----------------------------------------------------------------
    def add_event(self, ts, actor, action, details, project_id=None, task_id=None, signing_key=None):
        """Append to the activity history. Must be called inside a transaction.

        Entries by the human owner must be signed with the owner's key; no other
        entry may carry a signature.
        """
        assert self.conn.in_transaction, "events are only written inside a transaction"
        assert (actor == HUMAN) == (signing_key is not None), "only owner entries are signed, and all of them are"
        last = self.one("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1")
        seq = (last["seq"] + 1) if last else 1
        prev = last["hash"] if last else GENESIS_HASH
        details_json = dumps(details)
        mac = None
        if signing_key is not None:
            mac = sign(signing_key, signed_fields(prev, seq, ts, project_id, task_id, actor, action, details_json))
        digest = event_hash(prev, seq, ts, project_id, task_id, actor, action, details_json, mac)
        self.conn.execute(
            "INSERT INTO events (seq, ts, project_id, task_id, actor, action, details, prev_hash, hash, owner_mac) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (seq, ts, project_id, task_id, actor, action, details_json, prev, digest, mac),
        )
        return seq

    def verify_events(self):
        """Problems with the hash chain and with which entries carry owner signatures."""
        problems = []
        prev, expected_seq = GENESIS_HASH, 1
        for row in self.all("SELECT * FROM events ORDER BY seq"):
            if row["seq"] != expected_seq:
                problems.append(f"history entry {expected_seq} is missing")
            if row["prev_hash"] != prev:
                problems.append(f"history entry {row['seq']} does not follow the entry before it")
            digest = event_hash(row["prev_hash"], row["seq"], row["ts"], row["project_id"], row["task_id"],
                                row["actor"], row["action"], row["details"], row["owner_mac"])
            if digest != row["hash"]:
                problems.append(f"history entry {row['seq']} was changed after it was written")
            if (row["actor"] == HUMAN) != (row["owner_mac"] is not None):
                problems.append(f"history entry {row['seq']} has the wrong signature marking for {row['actor']}")
            prev, expected_seq = row["hash"], row["seq"] + 1
        return problems

    def signature_problems(self, key):
        """Owner entries whose signature does not match the owner's key."""
        problems = []
        for row in self.all("SELECT * FROM events WHERE owner_mac IS NOT NULL ORDER BY seq"):
            fields = signed_fields(row["prev_hash"], row["seq"], row["ts"], row["project_id"], row["task_id"],
                                   row["actor"], row["action"], row["details"])
            if not signature_matches(key, fields, row["owner_mac"]):
                problems.append(f"history entry {row['seq']} does not carry a valid owner signature")
        return problems

    def integrity_problems(self):
        result = [r[0] for r in self.all("PRAGMA integrity_check")]
        return [] if result == ["ok"] else result

    def anchor(self):
        """The newest history entry: keep it somewhere else to detect a rolled-back file."""
        row = self.one("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1")
        return (row["seq"], row["hash"]) if row else (0, GENESIS_HASH)

    def permission_warnings(self):
        """Ways other accounts on this computer could read or change the database."""
        if os.name != "posix":
            return []
        warnings = []
        info = os.stat(self.path)
        if info.st_mode & 0o077:
            warnings.append(f"{self.path} can be read or changed by other users (mode {oct(info.st_mode & 0o777)}); "
                            "run: chmod 600 " + self.path)
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            warnings.append(f"{self.path} belongs to another user account")
        directory = os.stat(os.path.dirname(self.path))
        if directory.st_mode & 0o022:
            warnings.append(f"the folder {os.path.dirname(self.path)} can be changed by other users")
        return warnings

    def backup(self, destination, *, allow_inside_git=False):
        destination = os.path.abspath(os.path.expanduser(destination))
        folder = os.path.dirname(destination)
        if not os.path.isdir(folder):
            raise StoreError(f"the folder {folder} does not exist")
        if not allow_inside_git and inside_git_work_tree(destination):
            raise StoreError(f"refusing to write a backup inside a git working tree ({destination})")
        try:
            _create_private_file(destination)
        except FileExistsError:
            raise StoreError(f"{destination} already exists; choose a new file name") from None
        except OSError as exc:
            raise StoreError(f"cannot create {destination}: {exc.strerror}") from None
        target = sqlite3.connect(destination)
        try:
            self.conn.backup(target)
        except sqlite3.Error as exc:
            raise StoreError(f"the backup failed: {exc}") from None
        finally:
            target.close()
        return destination
