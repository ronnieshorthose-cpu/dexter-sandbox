"""The coordinator: projects, tasks, approvals, handoffs and history.

Each public method is one action by one fixed role. There is no method that
takes the acting role as an argument, so nothing an AI writes can be turned
into a human action:

  - human_owner actions (approve, reject, complete, return_to_work, cancel,
    resume_project, record_response, mark_handoff_sent, passphrase change)
    require the owner passphrase. They are labelled SIMULATED, signed with the
    owner's key, and refuse to run while any stored record fails its checks;
  - dexter actions organise work. Anyone who can run the command line can run
    them, so they are recorded as "dexter (operator not verified)" and only move
    tasks between non-human states, each guarded by owner-recorded evidence;
  - local_operator entries (pausing, setting up the passphrase, wrong
    passphrases) were made by someone whose identity Dexter did not check;
  - ChatGPT and Claude Code never act. Their replies are pasted in and
    confirmed by the owner, and the sender they name is only a claim.
"""

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone

from . import handoff as ho
from .auth import (
    APPROVAL_LABEL,
    DEFAULT_COST,
    AuthError,
    check_new_passphrase,
    lockout_seconds,
    new_owner_record,
    unlock,
)
from .models import (
    ACTIONS,
    AI_ROLES,
    APPROVED,
    ARCHITECTURE_REVIEW,
    ASSIGNABLE_ROLES,
    AWAITING_APPROVAL,
    BACKLOG,
    BLOCKED,
    BLOCKING_SEVERITIES,
    CANCELLED,
    CHATGPT,
    CLAUDE,
    COMPLETED,
    DEXTER,
    EDITABLE_STATES,
    HANDOFF_KEY_RE,
    HUMAN,
    IMPLEMENT,
    IN_PROGRESS,
    IN_REVIEW,
    LIMITS,
    LOCAL_OPERATOR,
    PLANNED,
    PRIORITIES,
    PROJECT_KEY_RE,
    READY_FOR_HUMAN,
    REJECTED,
    REVIEW,
    STATES,
    TASK_KEY_RE,
    TERMINAL_STATES,
    IntegrityError,
    NotFound,
    RuleError,
    ValidationError,
    allowed_actor,
    clean_choice,
    clean_list,
    clean_text,
    role_title,
)
from .store import Store, dumps, sha256

ACTIVE = "ACTIVE"
PAUSED = "PAUSED"

# Which handoff actions make sense in which task states.
ACTION_STATES = {
    ARCHITECTURE_REVIEW: (BACKLOG, PLANNED, AWAITING_APPROVAL),
    IMPLEMENT: (IN_PROGRESS,),
    REVIEW: (IN_REVIEW,),
}
DEFAULT_ACTION = {
    BACKLOG: ARCHITECTURE_REVIEW,
    PLANNED: ARCHITECTURE_REVIEW,
    AWAITING_APPROVAL: ARCHITECTURE_REVIEW,
    IN_PROGRESS: IMPLEMENT,
    IN_REVIEW: REVIEW,
}
# States a task can only reach after a human approval.
APPROVED_STATES = (APPROVED, IN_PROGRESS, IN_REVIEW, READY_FOR_HUMAN, COMPLETED)
OPEN_STATES = tuple(s for s in STATES if s not in TERMINAL_STATES + (BLOCKED,))


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _key(value, pattern, what):
    if not isinstance(value, str):
        raise ValidationError(f"{what} ID must be text")
    value = value.strip().upper()
    if not pattern.fullmatch(value):
        raise ValidationError(f"{safe_id(value)} is not a valid {what} ID")
    return value


def safe_id(value):
    return json.dumps(str(value)[:40], ensure_ascii=True)


def response_digest(handoff_key, from_role, structured, outcome, data_json, raw, flags_json):
    """What the owner's 'response recorded' entry vouches for."""
    return sha256([handoff_key, from_role, bool(structured), outcome, data_json, raw, flags_json])


class Coordinator:
    def __init__(self, path, *, clock=utc_now, kdf_cost=DEFAULT_COST, now_seconds=time.time, allow_inside_git=False):
        self.store = Store(path, allow_inside_git=allow_inside_git)
        self.clock = clock
        self.kdf_cost = kdf_cost
        self.now_seconds = now_seconds
        self.allow_inside_git = allow_inside_git

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- helpers ---------------------------------------------------------------
    def _event(self, actor, action, details, project_id=None, task_id=None, key=None):
        return self.store.add_event(self.clock(), actor, action, details, project_id, task_id, signing_key=key)

    def _project_row(self, key):
        key = _key(key, PROJECT_KEY_RE, "project")
        row = self.store.one("SELECT * FROM projects WHERE key = ?", (key,))
        if row is None:
            raise NotFound(f"there is no project {key}")
        return row

    def _task_row(self, key):
        key = _key(key, TASK_KEY_RE, "task")
        row = self.store.one("SELECT * FROM tasks WHERE key = ?", (key,))
        if row is None:
            raise NotFound(f"there is no task {key}")
        return row

    def _handoff_row(self, key):
        key = _key(key, HANDOFF_KEY_RE, "handoff")
        row = self.store.one(
            "SELECT h.*, t.key AS task_key, p.key AS project_key FROM handoffs h "
            "JOIN tasks t ON t.id = h.task_id JOIN projects p ON p.id = h.project_id WHERE h.key = ?",
            (key,),
        )
        if row is None:
            raise NotFound(f"there is no handoff {key}")
        return row

    def _require_active(self, project_id, doing):
        row = self.store.one("SELECT key, status FROM projects WHERE id = ?", (project_id,))
        if row["status"] != ACTIVE:
            raise RuleError(
                f"project {row['key']} is paused, so Dexter cannot {doing}. "
                "The human owner can resume it with `project resume`."
            )

    def _content_hash(self, row):
        deps = [d["key"] for d in self._dependency_rows(row["id"])]
        content = {
            "name": row["name"],
            "description": row["description"],
            "priority": row["priority"],
            "assignee": row["assignee"],
            "criteria": json.loads(row["criteria"]),
            "depends_on": sorted(deps),
        }
        return hashlib.sha256(dumps(content).encode("utf-8")).hexdigest()

    def _dependency_rows(self, task_id):
        return self.store.all(
            "SELECT t.id, t.key, t.name, t.state FROM dependencies d JOIN tasks t ON t.id = d.depends_on "
            "WHERE d.task_id = ? ORDER BY t.id",
            (task_id,),
        )

    def _move(self, row, to_state, actor, details=None, *, expect, key=None, **updates):
        """Change a task's state if it is in one of the `expect` states and the rules
        allow this actor to make the move. Must run inside a transaction."""
        from_state = row["state"]
        if from_state == BLOCKED and BLOCKED not in expect and to_state != CANCELLED:
            raise RuleError(f"{row['key']} is BLOCKED ({row['blocked_reason']}). Clear the blocker and run "
                            "`task unblock` first")
        if from_state in TERMINAL_STATES:
            raise RuleError(f"{row['key']} is {from_state}, which is final; it cannot change any more")
        if from_state not in expect:
            needed = " or ".join(expect) if len(expect) <= 2 else "an open state"
            raise RuleError(f"{row['key']} is {from_state}; this step needs it to be {needed}")
        current = self.store.one("SELECT state FROM tasks WHERE id = ?", (row["id"],))["state"]
        if current != from_state:
            raise RuleError(f"{row['key']} was changed by another command (it is now {current}); try again")
        if allowed_actor(from_state, to_state, row["blocked_from"]) != actor:
            raise RuleError(_transition_refusal(row["key"], from_state, to_state, actor, row["blocked_from"]))
        info = {"from": from_state, "to": to_state}
        info.update(details or {})
        seq = self._event(actor, "state_changed", info, row["project_id"], row["id"], key=key)
        updates.update(state=to_state, state_seq=seq, updated_at=self.clock())
        columns = ", ".join(f"{name} = ?" for name in updates)
        self.store.conn.execute(f"UPDATE tasks SET {columns} WHERE id = ?", (*updates.values(), row["id"]))
        return seq

    # -- owner -----------------------------------------------------------------
    def _owner_row(self):
        return self.store.one("SELECT * FROM owner WHERE id = 1")

    def owner_is_set(self):
        return self._owner_row() is not None

    def _authenticate(self, passphrase, purpose):
        """Check the owner passphrase and return the owner's signing key.

        Must not run inside a transaction: a wrong passphrase is recorded even
        though the action fails. After two wrong passphrases in a row, owner
        actions are refused for a growing period (up to 15 minutes).
        """
        row = self._owner_row()
        if row is None:
            raise AuthError("no owner passphrase is set up yet. Run `init` first")
        now = self.now_seconds()
        if now < row["locked_until"]:
            wait = int(row["locked_until"] - now) + 1
            raise AuthError(f"too many wrong passphrases; wait {wait} seconds and try again. Nothing was changed")
        key = unlock(passphrase, row)
        if key is None:
            failures = row["failed_attempts"] + 1
            delay = lockout_seconds(failures)
            with self.store.transaction() as conn:
                conn.execute("UPDATE owner SET failed_attempts = ?, locked_until = ? WHERE id = 1",
                             (failures, now + delay, ))
                self._event(LOCAL_OPERATOR, "owner_check_failed",
                            {"purpose": purpose, "failures_in_a_row": failures, "wait_seconds": delay})
            extra = f" Owner actions are paused for {delay} seconds." if delay else ""
            raise AuthError(f"the owner passphrase is incorrect; nothing was changed.{extra}")
        if row["failed_attempts"]:
            with self.store.transaction() as conn:
                conn.execute("UPDATE owner SET failed_attempts = 0, locked_until = 0 WHERE id = 1")
        return key

    def _owner_key(self, passphrase, purpose):
        """Authenticate, then refuse to act on records that fail any check."""
        key = self._authenticate(passphrase, purpose)
        problems = self._structural_problems() + self.store.signature_problems(key)
        if problems:
            shown = "; ".join(problems[:3]) + (f" (and {len(problems) - 3} more)" if len(problems) > 3 else "")
            raise IntegrityError(
                f"the stored records fail Dexter's checks, so owner actions are refused: {shown}. "
                "Run `check --owner` and see \"Recovering from errors\" in docs/COORDINATOR.md"
            )
        return key

    def set_owner_passphrase(self, passphrase):
        """Set up the owner passphrase. Whoever does this first becomes the owner as far as Dexter can tell."""
        if self.owner_is_set():
            raise RuleError("an owner passphrase is already set. Use `owner change-passphrase` to change it")
        record = new_owner_record(passphrase, self.kdf_cost)
        with self.store.transaction() as conn:
            if self.owner_is_set():
                raise RuleError("an owner passphrase is already set")
            conn.execute(
                "INSERT INTO owner (id, kdf, salt, cost, verifier, wrapped_key, created_at) VALUES (1, ?, ?, ?, ?, ?, ?)",
                (record["kdf"], record["salt"], record["cost"], record["verifier"], record["wrapped_key"], self.clock()),
            )
            self._event(LOCAL_OPERATOR, "owner_passphrase_set", {
                "note": "whoever knows this passphrase is treated as the owner; the person who set it was not verified",
            })

    def change_owner_passphrase(self, old, new):
        check_new_passphrase(new)
        key = self._owner_key(old, "change the owner passphrase")
        record = new_owner_record(new, self.kdf_cost, signing_key=key)  # same key, re-wrapped
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE owner SET kdf = ?, salt = ?, cost = ?, verifier = ?, wrapped_key = ? WHERE id = 1",
                (record["kdf"], record["salt"], record["cost"], record["verifier"], record["wrapped_key"]),
            )
            self._event(HUMAN, "owner_passphrase_changed", {"label": APPROVAL_LABEL}, key=key)

    # -- projects --------------------------------------------------------------
    def create_project(self, name, description=""):
        name = clean_text(name, "project name", max_len=LIMITS["project_name"])
        description = clean_text(
            description, "project description", max_len=LIMITS["description"], required=False, multiline=True
        )
        now = self.clock()
        with self.store.transaction() as conn:
            if self.store.one("SELECT 1 FROM projects WHERE name = ?", (name,)):
                raise ValidationError(f'a project named "{name}" already exists')
            cur = conn.execute(
                "INSERT INTO projects (name, description, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (name, description, ACTIVE, now, now),
            )
            key = f"PRJ-{cur.lastrowid:03d}"
            conn.execute("UPDATE projects SET key = ? WHERE id = ?", (key, cur.lastrowid))
            self._event(DEXTER, "project_created", {"project": key, "name": name}, cur.lastrowid)
        return self.get_project(key)

    def get_project(self, key):
        row = self._project_row(key)
        return {
            "id": row["key"],
            "name": row["name"],
            "description": row["description"],
            "status": row["status"],
            "pause_reason": row["pause_reason"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_projects(self):
        return [self.get_project(r["key"]) for r in self.store.all("SELECT key FROM projects ORDER BY id")]

    def pause_project(self, key, reason):
        """Stop all coordinator activity. No passphrase: pausing only stops work, so anyone may do it."""
        reason = clean_text(reason, "reason", max_len=LIMITS["reason"], multiline=True)
        with self.store.transaction() as conn:
            row = self._project_row(key)
            if row["status"] == PAUSED:
                raise RuleError(f"project {row['key']} is already paused")
            conn.execute(
                "UPDATE projects SET status = ?, pause_reason = ?, updated_at = ? WHERE id = ?",
                (PAUSED, reason, self.clock(), row["id"]),
            )
            self._event(
                LOCAL_OPERATOR, "project_paused",
                {"reason": reason, "note": "pausing needs no passphrase because it only stops work"}, row["id"],
            )
        return self.get_project(key)

    def resume_project(self, key, passphrase):
        row = self._project_row(key)
        if row["status"] != PAUSED:
            raise RuleError(f"project {row['key']} is not paused")
        owner_key = self._owner_key(passphrase, f"resume project {row['key']}")
        with self.store.transaction() as conn:
            row = self._project_row(key)
            if row["status"] != PAUSED:
                raise RuleError(f"project {row['key']} is not paused")
            conn.execute(
                "UPDATE projects SET status = ?, pause_reason = NULL, updated_at = ? WHERE id = ?",
                (ACTIVE, self.clock(), row["id"]),
            )
            self._event(HUMAN, "project_resumed", {"label": APPROVAL_LABEL}, row["id"], key=owner_key)
        return self.get_project(key)

    # -- tasks: content ----------------------------------------------------------
    def create_task(self, project_key, name, description, *, priority="medium", criteria=(), depends_on=(), assignee=None):
        name = clean_text(name, "task name", max_len=LIMITS["task_name"])
        description = clean_text(description, "description", max_len=LIMITS["description"], multiline=True)
        priority = clean_choice(priority, "priority", PRIORITIES)
        criteria = clean_list(list(criteria), "acceptance criteria", max_items=LIMITS["criteria"], max_len=LIMITS["criterion"])
        if assignee is not None:
            assignee = _assignable(assignee)
        now = self.clock()
        with self.store.transaction() as conn:
            project = self._project_row(project_key)
            dep_rows = []
            for dep_key in depends_on:
                dep = self._task_row(dep_key)
                if dep["project_id"] != project["id"]:
                    raise RuleError(f"{dep['key']} belongs to a different project; dependencies must be in the same project")
                if dep["id"] not in [d["id"] for d in dep_rows]:
                    dep_rows.append(dep)
            cur = conn.execute(
                "INSERT INTO tasks (project_id, name, description, priority, assignee, criteria, state, state_seq, "
                "approval, acceptance, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                (project["id"], name, description, priority, assignee, dumps(criteria), BACKLOG,
                 dumps({"status": "not_requested"}), dumps({"status": "not_requested"}), now, now),
            )
            task_id = cur.lastrowid
            key = f"TASK-{task_id:04d}"
            for dep in dep_rows:
                conn.execute("INSERT INTO dependencies (task_id, depends_on) VALUES (?, ?)", (task_id, dep["id"]))
            seq = self._event(
                DEXTER, "task_created",
                {"task": key, "name": name, "priority": priority, "assignee": assignee,
                 "depends_on": [d["key"] for d in dep_rows]},
                project["id"], task_id,
            )
            conn.execute("UPDATE tasks SET key = ?, state_seq = ? WHERE id = ?", (key, seq, task_id))
        return self.get_task(key)

    def _require_editable(self, row):
        if row["state"] not in EDITABLE_STATES:
            hint = " Withdraw it first (`task withdraw`)." if row["state"] == AWAITING_APPROVAL else ""
            raise RuleError(
                f"{row['key']} is {row['state']}. A task's content can only change while it is BACKLOG or "
                f"PLANNED, so an approval always covers exactly what is built.{hint}"
            )

    def edit_task(self, key, *, name=None, description=None, priority=None):
        changes = {}
        if name is not None:
            changes["name"] = clean_text(name, "task name", max_len=LIMITS["task_name"])
        if description is not None:
            changes["description"] = clean_text(description, "description", max_len=LIMITS["description"], multiline=True)
        if priority is not None:
            changes["priority"] = clean_choice(priority, "priority", PRIORITIES)
        if not changes:
            raise ValidationError("nothing to change; give a new name, description or priority")
        with self.store.transaction() as conn:
            row = self._task_row(key)
            self._require_editable(row)
            columns = ", ".join(f"{c} = ?" for c in changes)
            conn.execute(f"UPDATE tasks SET {columns}, updated_at = ? WHERE id = ?",
                         (*changes.values(), self.clock(), row["id"]))
            self._event(DEXTER, "task_edited", {"fields": sorted(changes)}, row["project_id"], row["id"])
        return self.get_task(key)

    def add_criterion(self, key, text):
        text = clean_text(text, "acceptance criterion", max_len=LIMITS["criterion"])
        with self.store.transaction() as conn:
            row = self._task_row(key)
            self._require_editable(row)
            criteria = json.loads(row["criteria"])
            if len(criteria) >= LIMITS["criteria"]:
                raise ValidationError(f"a task can have at most {LIMITS['criteria']} acceptance criteria")
            criteria.append(text)
            conn.execute("UPDATE tasks SET criteria = ?, updated_at = ? WHERE id = ?",
                         (dumps(criteria), self.clock(), row["id"]))
            self._event(DEXTER, "criterion_added", {"criterion": text}, row["project_id"], row["id"])
        return self.get_task(key)

    def assign(self, key, role):
        role = _assignable(role)
        with self.store.transaction() as conn:
            row = self._task_row(key)
            self._require_editable(row)
            conn.execute("UPDATE tasks SET assignee = ?, updated_at = ? WHERE id = ?", (role, self.clock(), row["id"]))
            self._event(DEXTER, "task_assigned", {"assignee": role, "previous": row["assignee"]},
                        row["project_id"], row["id"])
        return self.get_task(key)

    def add_dependency(self, key, depends_on_key):
        with self.store.transaction() as conn:
            row = self._task_row(key)
            self._require_editable(row)
            dep = self._task_row(depends_on_key)
            if dep["id"] == row["id"]:
                raise RuleError("a task cannot depend on itself")
            if dep["project_id"] != row["project_id"]:
                raise RuleError(f"{dep['key']} belongs to a different project; dependencies must be in the same project")
            if self.store.one("SELECT 1 FROM dependencies WHERE task_id = ? AND depends_on = ?", (row["id"], dep["id"])):
                raise RuleError(f"{row['key']} already depends on {dep['key']}")
            if self._reaches(dep["id"], row["id"]):
                raise RuleError(f"{row['key']} cannot depend on {dep['key']}: {dep['key']} already depends on "
                                f"{row['key']}, which would make a loop")
            conn.execute("INSERT INTO dependencies (task_id, depends_on) VALUES (?, ?)", (row["id"], dep["id"]))
            self._event(DEXTER, "dependency_added", {"depends_on": dep["key"]}, row["project_id"], row["id"])
        return self.get_task(key)

    def remove_dependency(self, key, depends_on_key):
        with self.store.transaction() as conn:
            row = self._task_row(key)
            self._require_editable(row)
            dep = self._task_row(depends_on_key)
            cur = conn.execute("DELETE FROM dependencies WHERE task_id = ? AND depends_on = ?", (row["id"], dep["id"]))
            if cur.rowcount == 0:
                raise RuleError(f"{row['key']} does not depend on {dep['key']}")
            self._event(DEXTER, "dependency_removed", {"depends_on": dep["key"]}, row["project_id"], row["id"])
        return self.get_task(key)

    def _reaches(self, start_id, target_id):
        """True if start_id depends (directly or indirectly) on target_id."""
        seen, todo = set(), [start_id]
        while todo:
            current = todo.pop()
            if current == target_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            todo += [r[0] for r in self.store.all("SELECT depends_on FROM dependencies WHERE task_id = ?", (current,))]
        return False

    # -- tasks: workflow (Dexter) ------------------------------------------------
    # Each step reads the task, checks its guards and moves it inside one
    # transaction, so another process cannot change the task in between.
    def _ready_to_plan(self, row):
        missing = []
        if not row["assignee"]:
            missing.append("an assigned AI role (`task assign`)")
        if not json.loads(row["criteria"]):
            missing.append("at least one acceptance criterion (`task add-criterion`)")
        if missing:
            raise RuleError(f"{row['key']} needs " + " and ".join(missing))

    def _require_consistent(self, row):
        """Refuse to move a task whose stored records disagree with its history."""
        problems = self.store.verify_events() + self._task_problems(row)
        if problems:
            raise IntegrityError(f"{row['key']}'s records fail Dexter's checks: {'; '.join(problems[:3])}. Run `check`")

    def plan(self, key):
        with self.store.transaction():
            row = self._task_row(key)
            self._require_active(row["project_id"], "plan tasks")
            self._ready_to_plan(row)
            self._move(row, PLANNED, DEXTER, expect=(BACKLOG,))
        return self.get_task(key)

    def unplan(self, key):
        with self.store.transaction():
            row = self._task_row(key)
            self._require_active(row["project_id"], "change tasks")
            self._move(row, BACKLOG, DEXTER, expect=(PLANNED,))
        return self.get_task(key)

    def submit_for_approval(self, key):
        with self.store.transaction():
            row = self._task_row(key)
            self._require_active(row["project_id"], "request approvals")
            self._ready_to_plan(row)
            self._move(row, AWAITING_APPROVAL, DEXTER, {"content_hash": self._content_hash(row)},
                       expect=(PLANNED,), approval=dumps({"status": "awaiting", "requested_at": self.clock()}))
        return self.get_task(key)

    def withdraw(self, key):
        with self.store.transaction():
            row = self._task_row(key)
            self._require_active(row["project_id"], "change tasks")
            self._move(row, PLANNED, DEXTER, expect=(AWAITING_APPROVAL,), approval=dumps({"status": "not_requested"}))
        return self.get_task(key)

    def start(self, key):
        with self.store.transaction():
            row = self._task_row(key)
            self._require_active(row["project_id"], "start tasks")
            if row["state"] == APPROVED:
                self._require_consistent(row)
                waiting = [d for d in self._dependency_rows(row["id"]) if d["state"] != COMPLETED]
                if waiting:
                    names = ", ".join(f"{d['key']} ({d['state']})" for d in waiting)
                    hint = ""
                    if any(d["state"] in (CANCELLED, REJECTED) for d in waiting):
                        hint = " A dependency was cancelled or rejected, so the owner may need to cancel this task."
                    raise RuleError(f"{row['key']} is waiting for its dependencies to be COMPLETED: {names}.{hint}")
            self._move(row, IN_PROGRESS, DEXTER, expect=(APPROVED,))
        return self.get_task(key)

    def send_to_review(self, key):
        """IN_PROGRESS -> IN_REVIEW, once the owner has recorded a 'done' report from the assignee."""
        with self.store.transaction():
            row = self._task_row(key)
            self._require_active(row["project_id"], "send tasks to review")
            if row["state"] == IN_PROGRESS:
                self._require_consistent(row)
                report = self._latest_response(row, IMPLEMENT)
                if report is None:
                    raise RuleError(f"{row['key']} has no implementation report recorded since it entered IN_PROGRESS. "
                                    "Create an `implement` handoff and record the reply first")
                if not report["structured"]:
                    raise RuleError("the latest implementation report was recorded as unstructured text, so Dexter "
                                    "cannot check it. Ask for a reply in the handoff format and record that")
                if report["from_role"] != row["assignee"]:
                    raise RuleError(f"the latest implementation report is not addressed from the assigned role "
                                    f"({row['assignee']})")
                if report["outcome"] != "done":
                    raise RuleError(f"the latest implementation report says {report['outcome']!r}, not 'done'")
                if row["assignee"] == CLAUDE:
                    tests = json.loads(report["data"]).get("tests", [])
                    if not any(t["result"] == "passed" for t in tests):
                        raise RuleError("the implementation report has no passing tests; Claude Code must report "
                                        "test results")
                    failed = [t["name"] for t in tests if t["result"] == "failed"]
                    if failed:
                        raise RuleError(f"the implementation report has failing tests: {', '.join(failed[:5])}")
            self._move(row, IN_REVIEW, DEXTER, expect=(IN_PROGRESS,))
        return self.get_task(key)

    def request_changes(self, key, reason):
        reason = clean_text(reason, "reason", max_len=LIMITS["reason"], multiline=True)
        with self.store.transaction():
            row = self._task_row(key)
            self._require_active(row["project_id"], "send tasks back")
            self._move(row, IN_PROGRESS, DEXTER, {"reason": reason}, expect=(IN_REVIEW,))
        return self.get_task(key)

    def mark_ready(self, key):
        """IN_REVIEW -> READY_FOR_HUMAN, once the owner has recorded an independent review recommending acceptance."""
        with self.store.transaction():
            row = self._task_row(key)
            self._require_active(row["project_id"], "prepare tasks for the owner")
            if row["state"] == IN_REVIEW:
                self._require_consistent(row)
                review = self._latest_response(row, REVIEW)
                if review is None:
                    raise RuleError(f"{row['key']} has no review recorded since it entered IN_REVIEW. "
                                    "Create a `review` handoff and record the reply first")
                if not review["structured"]:
                    raise RuleError("the latest review was recorded as unstructured text, so Dexter cannot check it")
                if review["from_role"] == row["assignee"] or review["from_role"] not in AI_ROLES:
                    raise RuleError("the review must be addressed to a different AI role than the one that did the work")
                if review["outcome"] != "recommend_accept":
                    raise RuleError(f"the latest review says {review['outcome']!r}; use `task request-changes` instead")
                findings = json.loads(review["data"]).get("findings", [])
                serious = [f for f in findings if f["severity"] in BLOCKING_SEVERITIES]
                if serious:
                    raise RuleError(f"the review recommends acceptance but lists {len(serious)} critical or major "
                                    "finding(s); ask the reviewer to resolve the contradiction")
            self._move(row, READY_FOR_HUMAN, DEXTER, expect=(IN_REVIEW,))
        return self.get_task(key)

    def block(self, key, reason):
        """Dexter: stop a task until a blocker is cleared. Allowed while paused (it only stops work)."""
        reason = clean_text(reason, "reason", max_len=LIMITS["reason"], multiline=True)
        with self.store.transaction():
            row = self._task_row(key)
            self._move(row, BLOCKED, DEXTER, {"reason": reason}, expect=OPEN_STATES,
                       blocked_from=row["state"], blocked_reason=reason)
        return self.get_task(key)

    def unblock(self, key):
        with self.store.transaction():
            row = self._task_row(key)
            self._require_active(row["project_id"], "unblock tasks")
            if row["state"] != BLOCKED:
                raise RuleError(f"{row['key']} is not blocked")
            self._move(row, row["blocked_from"], DEXTER, expect=(BLOCKED,), blocked_from=None, blocked_reason=None)
        return self.get_task(key)

    # -- tasks: human owner decisions (SIMULATED, signed) --------------------------
    def check_human_decision(self, key, to_state):
        """Raise RuleError unless the human owner may move this task to to_state now.

        Lets the command line refuse before asking for the passphrase.
        """
        row = self._task_row(key)
        if row["state"] == BLOCKED and to_state != CANCELLED:
            raise RuleError(f"{row['key']} is BLOCKED ({row['blocked_reason']}); it must be unblocked first")
        if allowed_actor(row["state"], to_state, row["blocked_from"]) != HUMAN:
            raise RuleError(_transition_refusal(row["key"], row["state"], to_state, HUMAN, row["blocked_from"]))
        return row

    def _human_move(self, key, to_state, passphrase, purpose, *, reason=None, note=None, record=None):
        """Owner decision. `record` names the task field ('approval' or 'acceptance') that
        will point at the signed history entry, with the given extra fields."""
        self.check_human_decision(key, to_state)
        owner_key = self._owner_key(passphrase, f"{purpose} {key}")
        with self.store.transaction() as conn:
            row = self.check_human_decision(key, to_state)
            details = {"label": APPROVAL_LABEL}
            if reason:
                details["reason"] = reason
            if note:
                details["note"] = note
            content_hash = self._content_hash(row)
            if to_state == APPROVED:
                details["content_hash"] = content_hash
            updates = {"blocked_from": None, "blocked_reason": None} if to_state == CANCELLED else {}
            seq = self._move(row, to_state, HUMAN, details, expect=(row["state"],), key=owner_key, **updates)
            if record:
                field, extra = record
                value = {"at": self.clock(), "label": APPROVAL_LABEL, "event_seq": seq, **extra}
                if field == "approval" and to_state == APPROVED:
                    value["content_hash"] = content_hash
                conn.execute(f"UPDATE tasks SET {field} = ? WHERE id = ?", (dumps(value), row["id"]))
        return self.get_task(key)

    def approve(self, key, passphrase, note=""):
        note = clean_text(note, "note", max_len=LIMITS["reason"], required=False, multiline=True)
        return self._human_move(key, APPROVED, passphrase, "approve", note=note,
                                record=("approval", {"status": "approved", "note": note}))

    def reject(self, key, passphrase, reason):
        reason = clean_text(reason, "reason", max_len=LIMITS["reason"], multiline=True)
        return self._human_move(key, REJECTED, passphrase, "reject", reason=reason,
                                record=("approval", {"status": "rejected", "reason": reason}))

    def complete(self, key, passphrase, note=""):
        """Human owner accepts the work. Records the decision in Dexter only; it merges and deploys nothing."""
        note = clean_text(note, "note", max_len=LIMITS["reason"], required=False, multiline=True)
        return self._human_move(key, COMPLETED, passphrase, "accept", note=note, record=("acceptance", {
            "status": "accepted", "note": note, "scope": "Recorded in Dexter only. Nothing was merged or deployed.",
        }))

    def return_to_work(self, key, passphrase, reason):
        reason = clean_text(reason, "reason", max_len=LIMITS["reason"], multiline=True)
        return self._human_move(key, IN_PROGRESS, passphrase, "send back", reason=reason)

    def cancel(self, key, passphrase, reason):
        reason = clean_text(reason, "reason", max_len=LIMITS["reason"], multiline=True)
        return self._human_move(key, CANCELLED, passphrase, "cancel", reason=reason)

    # -- task views --------------------------------------------------------------
    def get_task(self, key):
        row = self._task_row(key)
        project = self.store.one("SELECT key, name, status FROM projects WHERE id = ?", (row["project_id"],))
        dependents = self.store.all(
            "SELECT t.key FROM dependencies d JOIN tasks t ON t.id = d.task_id WHERE d.depends_on = ? ORDER BY t.id",
            (row["id"],),
        )
        report = self._latest_response(row, IMPLEMENT, current_only=False)
        review = self._latest_response(row, REVIEW, current_only=False)
        structured_report = report is not None and report["structured"]
        structured_review = review is not None and review["structured"]
        return {
            "id": row["key"],
            "project": project["key"],
            "project_name": project["name"],
            "project_status": project["status"],
            "name": row["name"],
            "description": row["description"],
            "priority": row["priority"],
            "assignee": row["assignee"],
            "criteria": json.loads(row["criteria"]),
            "state": row["state"],
            "blocked_from": row["blocked_from"],
            "blocked_reason": row["blocked_reason"],
            "depends_on": [{"id": d["key"], "name": d["name"], "state": d["state"]} for d in self._dependency_rows(row["id"])],
            "dependents": [d["key"] for d in dependents],
            "approval": json.loads(row["approval"]),
            "acceptance": json.loads(row["acceptance"]),
            # Replies name their sender, but Dexter cannot check who wrote them:
            # these are claims, recorded by the owner.
            "test_results": json.loads(report["data"]).get("tests", []) if structured_report else [],
            "tests_claimed_by": report["from_role"] if structured_report else None,
            "review_findings": json.loads(review["data"]).get("findings", []) if structured_review else [],
            "review_claimed_by": review["from_role"] if structured_review else None,
            "reviewer_verified": False,
            "review_outcome": review["outcome"] if review else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_tasks(self, project_key=None, state=None):
        sql, params = "SELECT t.key FROM tasks t", []
        clauses = []
        if project_key:
            clauses.append("t.project_id = ?")
            params.append(self._project_row(project_key)["id"])
        if state:
            clauses.append("t.state = ?")
            params.append(clean_choice(state.upper(), "state", STATES))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return [self.get_task(r["key"]) for r in self.store.all(sql + " ORDER BY t.id", params)]

    def _latest_response(self, row, action, current_only=True):
        """The newest reply for this kind of handoff. With current_only, only replies to
        handoffs prepared since the task entered its current state count as evidence."""
        sql = ("SELECT r.*, h.key AS handoff_key FROM responses r JOIN handoffs h ON h.id = r.handoff_id "
               "WHERE r.task_id = ? AND h.action = ?")
        params = [row["id"], action]
        if current_only:
            sql += " AND h.task_state_seq = ? AND r.seq > ?"
            params += [row["state_seq"], row["state_seq"]]
        return self.store.one(sql + " ORDER BY r.seq DESC LIMIT 1", params)

    def history(self, task_key=None, project_key=None, limit=None):
        sql, params = ("SELECT e.*, t.key AS task_key, p.key AS project_key FROM events e "
                       "LEFT JOIN tasks t ON t.id = e.task_id LEFT JOIN projects p ON p.id = e.project_id"), []
        if task_key:
            sql += " WHERE e.task_id = ?"
            params.append(self._task_row(task_key)["id"])
        elif project_key:
            sql += " WHERE e.project_id = ?"
            params.append(self._project_row(project_key)["id"])
        sql += " ORDER BY e.seq"
        rows = self.store.all(sql, params)
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise ValidationError("limit must be a whole number of at least 1")
            rows = rows[-limit:]
        return [
            {"seq": r["seq"], "ts": r["ts"], "actor": r["actor"], "action": r["action"], "task": r["task_key"],
             "project": r["project_key"], "details": json.loads(r["details"]), "signed": r["owner_mac"] is not None}
            for r in rows
        ]

    # -- handoffs ----------------------------------------------------------------
    def create_handoff(self, task_key, *, action=None, to_role=None, context="", references=()):
        context = clean_text(context, "context", max_len=LIMITS["context"], required=False, multiline=True)
        references = clean_list(list(references), "references", max_items=LIMITS["references"], max_len=LIMITS["reference"])
        with self.store.transaction() as conn:
            row = self._task_row(task_key)
            self._require_active(row["project_id"], "prepare handoffs")
            state = row["state"]
            if action is None:
                action = DEFAULT_ACTION.get(state)
                if action is None:
                    raise RuleError(f"{row['key']} is {state}; no AI handoff is needed in this state")
            action = clean_choice(action, "action", tuple(ACTION_STATES))
            if state not in ACTION_STATES[action]:
                allowed = ", ".join(ACTION_STATES[action])
                raise RuleError(f"a {action} handoff is only possible when the task is {allowed}; {row['key']} is {state}")
            to_role = self._recipient(row, action, to_role)
            project = self.store.one("SELECT key, name FROM projects WHERE id = ?", (row["project_id"],))
            correlation_id = str(uuid.uuid4())
            now = self.clock()

            trusted = [f"Task description:\n{row['description']}"]
            reason = self._latest_reason(row)
            if action == IMPLEMENT and reason:
                trusted.append(f"Changes requested ({reason['who']}):\n{reason['text']}")
            if context:
                trusted.append(f"Notes from the person running Dexter:\n{context}")
            untrusted = []
            if action == REVIEW:
                report = self._latest_response(row, IMPLEMENT, current_only=False)
                if report is not None and report["structured"]:
                    untrusted.append(_untrusted_item(report, IMPLEMENT, "implementation report"))
            if action == IMPLEMENT:
                review = self._latest_response(row, REVIEW, current_only=False)
                if review is not None and review["structured"] and review["outcome"] == "recommend_changes":
                    untrusted.append(_untrusted_item(review, REVIEW, "review findings"))

            cur = conn.execute(
                "INSERT INTO handoffs (correlation_id, project_id, task_id, from_role, to_role, action, payload, "
                "message, delivery, created_seq, task_state_seq, task_content_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, '{}', '', ?, 0, ?, ?, ?)",
                (correlation_id, row["project_id"], row["id"], DEXTER, to_role, action, ho.NOT_DELIVERED,
                 row["state_seq"], self._content_hash(row), now),
            )
            key = f"HO-{cur.lastrowid:04d}"
            payload = {
                "format": ho.HANDOFF_FORMAT,
                "handoff_id": key,
                "correlation_id": correlation_id,
                "created_at": now,
                "project": {"id": project["key"], "name": project["name"]},
                "task": {"id": row["key"], "name": row["name"], "priority": row["priority"], "state": state},
                "from_role": DEXTER,
                "to_role": to_role,
                "action": action,
                "requested_action": ACTIONS[action],
                "objective": ho.objective_for(action, row["name"]),
                "context": "\n\n".join(trusted),
                "untrusted_context": untrusted,
                "boundary": ho.new_boundary(),
                "references": references,
                "acceptance_criteria": json.loads(row["criteria"]),
                "constraints": ho.constraints_for(to_role),
                "response_format": ho.response_template(action, correlation_id, row["key"], to_role),
                "response": None,
            }
            message = ho.render_message(payload)
            seq = self._event(
                DEXTER, "handoff_created",
                {"handoff": key, "to": to_role, "action": action, "correlation_id": correlation_id,
                 "delivery": ho.NOT_DELIVERED, "message_sha256": sha256(message)},
                row["project_id"], row["id"],
            )
            conn.execute("UPDATE handoffs SET key = ?, payload = ?, message = ?, created_seq = ? WHERE id = ?",
                         (key, dumps(payload), message, seq, cur.lastrowid))
        return self.get_handoff(key)

    def _recipient(self, row, action, to_role):
        if to_role is not None:
            to_role = clean_choice(to_role, "recipient", AI_ROLES)
        if action == ARCHITECTURE_REVIEW:
            expected = CHATGPT
        elif action == IMPLEMENT:
            if not row["assignee"]:
                raise RuleError(f"{row['key']} has no assigned role")
            expected = row["assignee"]
        else:
            expected = next(r for r in AI_ROLES if r != row["assignee"])
            if to_role is not None and to_role == row["assignee"]:
                raise RuleError(f"{role_title(to_role)} did the work, so it cannot review it; the review must be independent")
        if to_role is not None and to_role != expected:
            raise RuleError(f"a {action} handoff for {row['key']} goes to {expected}, not {to_role}")
        return expected

    def _latest_reason(self, row):
        events = self.history(task_key=row["key"])
        for event in reversed(events):
            d = event["details"]
            if event["action"] == "state_changed" and d.get("to") == IN_PROGRESS and d.get("reason"):
                who = "human owner" if event["actor"] == HUMAN else "Dexter, after review"
                return {"who": who, "text": d["reason"]}
        return None

    def get_handoff(self, key):
        row = self._handoff_row(key)
        response = self.store.one("SELECT * FROM responses WHERE handoff_id = ?", (row["id"],))
        result = {
            "id": row["key"],
            "correlation_id": row["correlation_id"],
            "project": row["project_key"],
            "task": row["task_key"],
            "from_role": row["from_role"],
            "to_role": row["to_role"],
            "action": row["action"],
            "delivery": row["delivery"],
            "delivery_label": ho.DELIVERY_LABELS[row["delivery"]],
            "created_at": row["created_at"],
            "marked_sent_at": row["marked_sent_at"],
            "payload": json.loads(row["payload"]),
            "message": row["message"],
            "response": None,
        }
        if response is not None:
            result["response"] = {
                "claimed_from": response["from_role"],
                "sender_verified": False,
                "structured": bool(response["structured"]),
                "outcome": response["outcome"],
                "data": json.loads(response["data"]),
                "raw": response["raw"],
                "flags": json.loads(response["flags"]),
                "recorded_at": response["recorded_at"],
            }
        return result

    def list_handoffs(self, task_key=None):
        sql, params = "SELECT key FROM handoffs", []
        if task_key:
            sql += " WHERE task_id = ?"
            params.append(self._task_row(task_key)["id"])
        return [self.get_handoff(r["key"]) for r in self.store.all(sql + " ORDER BY id", params)]

    def mark_handoff_sent(self, key, passphrase=None):
        """The owner confirms they copied the message to the assistant. Not a confirmed delivery."""
        row = self._handoff_row(key)
        if row["delivery"] != ho.NOT_DELIVERED:
            raise RuleError(f"{row['key']} is already {row['delivery']}")
        owner_key = self._owner_key(passphrase, f"mark {row['key']} as sent")
        with self.store.transaction() as conn:
            row = self._handoff_row(key)
            if row["delivery"] != ho.NOT_DELIVERED:
                raise RuleError(f"{row['key']} is already {row['delivery']}")
            conn.execute("UPDATE handoffs SET delivery = ?, marked_sent_at = ? WHERE id = ?",
                         (ho.MARKED_SENT, self.clock(), row["id"]))
            self._event(HUMAN, "handoff_marked_sent",
                        {"handoff": row["key"], "label": APPROVAL_LABEL, "note": ho.DELIVERY_LABELS[ho.MARKED_SENT]},
                        row["project_id"], row["task_id"], key=owner_key)
        return self.get_handoff(key)

    def _check_reply_can_be_recorded(self, row):
        """Refuse replies that already exist, or that answer an older version of the task."""
        if self.store.one("SELECT 1 FROM responses WHERE handoff_id = ?", (row["id"],)):
            raise RuleError(f"a response is already recorded for {row['key']}; create a new handoff for a new reply")
        task = self.store.one("SELECT * FROM tasks WHERE id = ?", (row["task_id"],))
        if task["state_seq"] != row["task_state_seq"]:
            raise RuleError(
                f"{row['key']} was prepared before {task['key']} entered its current state ({task['state']}), so its "
                f"reply would describe an older situation. Create a new handoff with `handoff create {task['key']}`"
            )
        if self._content_hash(task) != row["task_content_hash"]:
            raise RuleError(
                f"{task['key']} was changed after {row['key']} was prepared, so the reply is about an older version. "
                f"Create a new handoff with `handoff create {task['key']}`"
            )

    def preview_response(self, key, text, *, structured=True):
        """Check a reply without saving it. Returns what would be recorded."""
        row = self._handoff_row(key)
        text = ho.check_raw_reply(text)
        self._check_reply_can_be_recorded(row)
        handoff = {"key": row["key"], "action": row["action"], "correlation_id": row["correlation_id"],
                   "task_key": row["task_key"], "to_role": row["to_role"]}
        data = ho.validate_response(ho.extract_response_object(text), handoff) if structured else {}
        outcome = data.pop("outcome", None)
        return {"handoff": row["key"], "task": row["task_key"], "claimed_from": row["to_role"], "structured": structured,
                "outcome": outcome, "data": data, "flags": ho.scan_for_instructions(text)}

    def record_response(self, key, text, passphrase=None, *, structured=True):
        """The owner records a reply they pasted in. Never changes a task's state.

        The reply names its sender, but nothing proves who wrote it: the owner's
        signature vouches only that the owner recorded this exact text.
        """
        preview = self.preview_response(key, text, structured=structured)
        owner_key = self._owner_key(passphrase, f"record the reply to {preview['handoff']}")
        now = self.clock()
        with self.store.transaction() as conn:
            row = self._handoff_row(key)
            self._check_reply_can_be_recorded(row)  # again, inside the transaction
            data_json, flags_json = dumps(preview["data"]), dumps(preview["flags"])
            digest = response_digest(row["key"], row["to_role"], structured, preview["outcome"], data_json, text,
                                     flags_json)
            seq = self._event(
                HUMAN, "response_recorded",
                {"handoff": row["key"], "claimed_from": row["to_role"], "structured": structured,
                 "outcome": preview["outcome"], "flags": [f["rule"] for f in preview["flags"]],
                 "response_sha256": digest, "label": APPROVAL_LABEL,
                 "note": "the owner recorded this reply; its sender is a claim Dexter cannot verify"},
                row["project_id"], row["task_id"], key=owner_key,
            )
            try:
                conn.execute(
                    "INSERT INTO responses (handoff_id, task_id, from_role, structured, outcome, data, raw, flags, "
                    "seq, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (row["id"], row["task_id"], row["to_role"], int(structured), preview["outcome"], data_json, text,
                     flags_json, seq, now),
                )
            except Exception as exc:  # sqlite3.IntegrityError: another process recorded one first
                if "UNIQUE" in str(exc):
                    raise RuleError(f"a response was recorded for {row['key']} at the same moment; "
                                    "nothing was changed") from None
                raise
            conn.execute("UPDATE handoffs SET delivery = ? WHERE id = ?", (ho.RESPONSE_RECORDED, row["id"]))
        return self.get_handoff(key)

    # -- checks ------------------------------------------------------------------
    def _task_problems(self, row, events=None):
        """Replay a task's history and compare it with its stored state and decisions."""
        key = row["key"]
        if events is None:
            events = self.store.all("SELECT * FROM events WHERE task_id = ? ORDER BY seq", (row["id"],))
        problems = []
        created = [e for e in events if e["action"] == "task_created"]
        if len(created) != 1:
            return [f"{key} does not have exactly one creation entry in the history"]
        state, blocked_from, last_seq = BACKLOG, None, created[0]["seq"]
        approval_event = acceptance_event = None
        for e in events:
            if e["action"] != "state_changed":
                continue
            d = json.loads(e["details"])
            to_state = d.get("to")
            if d.get("from") != state:
                problems.append(f"{key}: history entry {e['seq']} starts from {d.get('from')}, but the task was {state}")
            if allowed_actor(state, to_state, blocked_from) != e["actor"]:
                problems.append(f"{key}: history entry {e['seq']} is not a move {e['actor']} may make")
            if to_state == APPROVED and e["actor"] == HUMAN:
                approval_event = e
            if to_state == PLANNED and state == AWAITING_APPROVAL:
                approval_event = None
            if to_state == COMPLETED:
                acceptance_event = e
            blocked_from = state if to_state == BLOCKED else None
            state, last_seq = to_state, e["seq"]
        if row["state"] != state:
            problems.append(f"{key} is stored as {row['state']}, but its history ends at {state}")
        if row["state_seq"] != last_seq:
            problems.append(f"{key} points to history entry {row['state_seq']}, but its last state change in the "
                            f"history is entry {last_seq}")
        if row["state"] == BLOCKED and row["blocked_from"] != blocked_from:
            problems.append(f"{key}'s blocked state does not match its history")
        approval = json.loads(row["approval"])
        effective = row["blocked_from"] if row["state"] == BLOCKED else row["state"]
        needs_approval = effective in APPROVED_STATES or approval.get("status") == "approved"
        if needs_approval:
            if approval_event is None:
                problems.append(f"{key} is {row['state']} without an owner approval in its history")
            else:
                approved_hash = json.loads(approval_event["details"]).get("content_hash")
                if approval.get("event_seq") != approval_event["seq"] or approval.get("content_hash") != approved_hash:
                    problems.append(f"{key}'s approval record does not match the owner's approval in the history")
                elif self._content_hash(row) != approved_hash:
                    problems.append(f"{key}'s content changed after the owner approved it")
        acceptance = json.loads(row["acceptance"])
        if row["state"] == COMPLETED or acceptance.get("status") == "accepted":
            if acceptance_event is None or acceptance.get("event_seq") != acceptance_event["seq"]:
                problems.append(f"{key}'s acceptance record does not match the owner's decision in the history")
        return problems

    def _structural_problems(self):
        """Every check that does not need the owner's key."""
        problems = self.store.integrity_problems() + self.store.verify_events()
        events_by_seq = {e["seq"]: e for e in self.store.all("SELECT * FROM events")}
        for row in self.store.all("SELECT * FROM tasks ORDER BY id"):
            problems += self._task_problems(row)
        for h in self.store.all("SELECT h.*, t.key AS task_key FROM handoffs h JOIN tasks t ON t.id = h.task_id"):
            event = events_by_seq.get(h["created_seq"])
            details = json.loads(event["details"]) if event else {}
            if (event is None or event["action"] != "handoff_created" or details.get("handoff") != h["key"]
                    or details.get("correlation_id") != h["correlation_id"]
                    or details.get("message_sha256") != sha256(h["message"])):
                problems.append(f"{h['key']} does not match its creation entry in the history")
        for r in self.store.all("SELECT r.*, h.key AS handoff_key FROM responses r JOIN handoffs h ON h.id = r.handoff_id"):
            event = events_by_seq.get(r["seq"])
            details = json.loads(event["details"]) if event else {}
            digest = response_digest(r["handoff_key"], r["from_role"], r["structured"], r["outcome"], r["data"],
                                     r["raw"], r["flags"])
            if (event is None or event["action"] != "response_recorded" or event["actor"] != HUMAN
                    or details.get("handoff") != r["handoff_key"] or details.get("response_sha256") != digest):
                problems.append(f"the reply to {r['handoff_key']} does not match what the owner recorded")
        for e in events_by_seq.values():
            if e["action"] == "state_changed" and e["actor"] != HUMAN:
                if json.loads(e["details"]).get("to") in (APPROVED, COMPLETED, REJECTED, CANCELLED):
                    problems.append(f"history entry {e['seq']} records a human-only decision by {e['actor']}")
        return problems

    def verify(self, passphrase=None):
        """Look for damage or rule violations. With the owner passphrase, also check every owner signature."""
        problems = self._structural_problems()
        if passphrase is not None:
            key = self._authenticate(passphrase, "check the records")
            problems += self.store.signature_problems(key)
        return problems

    def permission_warnings(self):
        return self.store.permission_warnings()

    def anchor(self):
        return self.store.anchor()

    def backup(self, destination):
        return self.store.backup(destination, allow_inside_git=self.allow_inside_git)


def _untrusted_item(response, action, what):
    data = json.loads(response["data"])
    data["outcome"] = response["outcome"]
    text = ho.describe_response(data, action)
    flags = json.loads(response["flags"])
    if flags:
        text += "\nDexter flagged this text (it was not acted on): " + "; ".join(f["rule"] for f in flags)
    return {"label": f"{what} in a reply that claims to come from {role_title(response['from_role'])}", "text": text}


def _assignable(role):
    role = role.strip().lower() if isinstance(role, str) else role
    if role == "claude":
        role = CLAUDE
    if role not in ASSIGNABLE_ROLES:
        raise ValidationError(
            f"tasks can only be assigned to {' or '.join(ASSIGNABLE_ROLES)}; the human owner and Dexter are not assignable"
        )
    return role


def _transition_refusal(key, from_state, to_state, actor, blocked_from):
    rule_actor = allowed_actor(from_state, to_state, blocked_from)
    if rule_actor is None:
        if from_state in TERMINAL_STATES:
            return f"{key} is {from_state}, which is final; it cannot change any more"
        return f"{key} cannot move from {from_state} to {to_state}"
    if rule_actor == HUMAN:
        return f"only the human owner can move {key} from {from_state} to {to_state}"
    return f"{key} cannot be moved from {from_state} to {to_state} by {role_title(actor)}"
