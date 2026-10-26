"""The coordinator: projects, tasks, approvals, handoffs and history.

Each public method is one action by one fixed role. There is no method that
takes the acting role as an argument, so nothing an AI writes can be turned
into a human action:

  - human_owner methods (approve, reject, complete, return_to_work, cancel,
    resume_project) require the owner passphrase, and are labelled SIMULATED;
  - dexter methods organise work and only move tasks between non-human states,
    each guarded by recorded evidence;
  - ChatGPT and Claude Code only appear as the source of recorded responses,
    which are data and never change a task's state.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

from . import handoff as ho
from .auth import APPROVAL_LABEL, DEFAULT_ITERATIONS, AuthError, check_new_passphrase, new_owner_record, passphrase_matches
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
    PLANNED,
    PRIORITIES,
    PROJECT_KEY_RE,
    READY_FOR_HUMAN,
    REJECTED,
    REVIEW,
    STATES,
    TASK_KEY_RE,
    TERMINAL_STATES,
    NotFound,
    RuleError,
    ValidationError,
    allowed_actor,
    clean_choice,
    clean_list,
    clean_text,
    role_title,
)
from .store import Store, dumps

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


class Coordinator:
    def __init__(self, path, *, clock=utc_now, kdf_iterations=DEFAULT_ITERATIONS):
        self.store = Store(path)
        self.clock = clock
        self.kdf_iterations = kdf_iterations

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- helpers ---------------------------------------------------------------
    def _event(self, actor, action, details, project_id=None, task_id=None):
        return self.store.add_event(self.clock(), actor, action, details, project_id, task_id)

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
        row = self.store.one("SELECT key, status, pause_reason FROM projects WHERE id = ?", (project_id,))
        if row["status"] != ACTIVE:
            raise RuleError(
                f"project {row['key']} is paused, so Dexter cannot {doing}. "
                "The human owner can resume it with `project resume`."
            )

    def _authenticate(self, passphrase, purpose):
        """Check the owner passphrase. Returns the SIMULATED approval label."""
        row = self.store.one("SELECT * FROM owner WHERE id = 1")
        if row is None:
            raise AuthError("no owner passphrase is set up yet. Run `init` first")
        if not passphrase_matches(passphrase, row):
            with self.store.transaction():
                self._event("unknown", "owner_check_failed", {"purpose": purpose})
            raise AuthError("the owner passphrase is incorrect; nothing was changed")
        return APPROVAL_LABEL

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

    def _move(self, row, to_state, actor, details=None, *, expect, **updates):
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
        seq = self._event(actor, "state_changed", info, row["project_id"], row["id"])
        updates.update(state=to_state, state_seq=seq, updated_at=self.clock())
        columns = ", ".join(f"{name} = ?" for name in updates)
        self.store.conn.execute(f"UPDATE tasks SET {columns} WHERE id = ?", (*updates.values(), row["id"]))
        return seq

    # -- owner -----------------------------------------------------------------
    def owner_is_set(self):
        return self.store.one("SELECT 1 FROM owner WHERE id = 1") is not None

    def set_owner_passphrase(self, passphrase):
        if self.owner_is_set():
            raise RuleError("an owner passphrase is already set. Use `owner change-passphrase` to change it")
        record = new_owner_record(passphrase, self.kdf_iterations)
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO owner (id, salt, hash, iterations, created_at) VALUES (1, ?, ?, ?, ?)",
                (record["salt"], record["hash"], record["iterations"], self.clock()),
            )
            self._event(HUMAN, "owner_passphrase_set", {"note": "local passphrase for SIMULATED approvals"})

    def change_owner_passphrase(self, old, new):
        self._authenticate(old, "change the owner passphrase")
        check_new_passphrase(new)
        record = new_owner_record(new, self.kdf_iterations)
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE owner SET salt = ?, hash = ?, iterations = ? WHERE id = 1",
                (record["salt"], record["hash"], record["iterations"]),
            )
            self._event(HUMAN, "owner_passphrase_changed", {"label": APPROVAL_LABEL})

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
        """Human owner: stop all coordinator activity. No passphrase: pausing only stops work."""
        row = self._project_row(key)
        reason = clean_text(reason, "reason", max_len=LIMITS["reason"], multiline=True)
        if row["status"] == PAUSED:
            raise RuleError(f"project {row['key']} is already paused")
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE projects SET status = ?, pause_reason = ?, updated_at = ? WHERE id = ?",
                (PAUSED, reason, self.clock(), row["id"]),
            )
            self._event(
                HUMAN, "project_paused",
                {"reason": reason, "note": "pausing needs no passphrase because it only stops work"}, row["id"],
            )
        return self.get_project(row["key"])

    def resume_project(self, key, passphrase):
        row = self._project_row(key)
        if row["status"] != PAUSED:
            raise RuleError(f"project {row['key']} is not paused")
        label = self._authenticate(passphrase, f"resume project {row['key']}")
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE projects SET status = ?, pause_reason = NULL, updated_at = ? WHERE id = ?",
                (ACTIVE, self.clock(), row["id"]),
            )
            self._event(HUMAN, "project_resumed", {"label": label}, row["id"])
        return self.get_project(row["key"])

    # -- tasks: content ----------------------------------------------------------
    def create_task(self, project_key, name, description, *, priority="medium", criteria=(), depends_on=(), assignee=None):
        project = self._project_row(project_key)
        name = clean_text(name, "task name", max_len=LIMITS["task_name"])
        description = clean_text(description, "description", max_len=LIMITS["description"], multiline=True)
        priority = clean_choice(priority, "priority", PRIORITIES)
        criteria = clean_list(list(criteria), "acceptance criteria", max_items=LIMITS["criteria"], max_len=LIMITS["criterion"])
        if assignee is not None:
            assignee = _assignable(assignee)
        dep_rows = []
        for dep_key in depends_on:
            dep = self._task_row(dep_key)
            if dep["project_id"] != project["id"]:
                raise RuleError(f"{dep['key']} belongs to a different project; dependencies must be in the same project")
            if dep["id"] not in [d["id"] for d in dep_rows]:
                dep_rows.append(dep)
        now = self.clock()
        with self.store.transaction() as conn:
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
        row = self._task_row(key)
        self._require_editable(row)
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
            columns = ", ".join(f"{c} = ?" for c in changes)
            conn.execute(f"UPDATE tasks SET {columns}, updated_at = ? WHERE id = ?",
                         (*changes.values(), self.clock(), row["id"]))
            self._event(DEXTER, "task_edited", {"fields": sorted(changes)}, row["project_id"], row["id"])
        return self.get_task(row["key"])

    def add_criterion(self, key, text):
        row = self._task_row(key)
        self._require_editable(row)
        text = clean_text(text, "acceptance criterion", max_len=LIMITS["criterion"])
        criteria = json.loads(row["criteria"])
        if len(criteria) >= LIMITS["criteria"]:
            raise ValidationError(f"a task can have at most {LIMITS['criteria']} acceptance criteria")
        criteria.append(text)
        with self.store.transaction() as conn:
            conn.execute("UPDATE tasks SET criteria = ?, updated_at = ? WHERE id = ?",
                         (dumps(criteria), self.clock(), row["id"]))
            self._event(DEXTER, "criterion_added", {"criterion": text}, row["project_id"], row["id"])
        return self.get_task(row["key"])

    def assign(self, key, role):
        row = self._task_row(key)
        self._require_editable(row)
        role = _assignable(role)
        with self.store.transaction() as conn:
            conn.execute("UPDATE tasks SET assignee = ?, updated_at = ? WHERE id = ?", (role, self.clock(), row["id"]))
            self._event(DEXTER, "task_assigned", {"assignee": role, "previous": row["assignee"]},
                        row["project_id"], row["id"])
        return self.get_task(row["key"])

    def add_dependency(self, key, depends_on_key):
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
        with self.store.transaction() as conn:
            conn.execute("INSERT INTO dependencies (task_id, depends_on) VALUES (?, ?)", (row["id"], dep["id"]))
            self._event(DEXTER, "dependency_added", {"depends_on": dep["key"]}, row["project_id"], row["id"])
        return self.get_task(row["key"])

    def remove_dependency(self, key, depends_on_key):
        row = self._task_row(key)
        self._require_editable(row)
        dep = self._task_row(depends_on_key)
        with self.store.transaction() as conn:
            cur = conn.execute("DELETE FROM dependencies WHERE task_id = ? AND depends_on = ?", (row["id"], dep["id"]))
            if cur.rowcount == 0:
                raise RuleError(f"{row['key']} does not depend on {dep['key']}")
            self._event(DEXTER, "dependency_removed", {"depends_on": dep["key"]}, row["project_id"], row["id"])
        return self.get_task(row["key"])

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
    def _ready_to_plan(self, row):
        missing = []
        if not row["assignee"]:
            missing.append("an assigned AI role (`task assign`)")
        if not json.loads(row["criteria"]):
            missing.append("at least one acceptance criterion (`task add-criterion`)")
        if missing:
            raise RuleError(f"{row['key']} needs " + " and ".join(missing))

    def plan(self, key):
        row = self._task_row(key)
        self._require_active(row["project_id"], "plan tasks")
        self._ready_to_plan(row)
        with self.store.transaction():
            self._move(row, PLANNED, DEXTER, expect=(BACKLOG,))
        return self.get_task(row["key"])

    def unplan(self, key):
        row = self._task_row(key)
        self._require_active(row["project_id"], "change tasks")
        with self.store.transaction():
            self._move(row, BACKLOG, DEXTER, expect=(PLANNED,))
        return self.get_task(row["key"])

    def submit_for_approval(self, key):
        row = self._task_row(key)
        self._require_active(row["project_id"], "request approvals")
        self._ready_to_plan(row)
        with self.store.transaction():
            self._move(row, AWAITING_APPROVAL, DEXTER, {"content_hash": self._content_hash(row)},
                       expect=(PLANNED,), approval=dumps({"status": "awaiting", "requested_at": self.clock()}))
        return self.get_task(row["key"])

    def withdraw(self, key):
        row = self._task_row(key)
        self._require_active(row["project_id"], "change tasks")
        with self.store.transaction():
            self._move(row, PLANNED, DEXTER, expect=(AWAITING_APPROVAL,), approval=dumps({"status": "not_requested"}))
        return self.get_task(row["key"])

    def start(self, key):
        row = self._task_row(key)
        self._require_active(row["project_id"], "start tasks")
        approval = json.loads(row["approval"])
        if row["state"] == APPROVED:
            if approval.get("status") != "approved" or approval.get("content_hash") != self._content_hash(row):
                raise RuleError(f"{row['key']} does not match what the human owner approved; it cannot start")
            waiting = [d for d in self._dependency_rows(row["id"]) if d["state"] != COMPLETED]
            if waiting:
                names = ", ".join(f"{d['key']} ({d['state']})" for d in waiting)
                hint = ""
                if any(d["state"] in (CANCELLED, REJECTED) for d in waiting):
                    hint = " A dependency was cancelled or rejected, so the owner may need to cancel this task."
                raise RuleError(f"{row['key']} is waiting for its dependencies to be COMPLETED: {names}.{hint}")
        with self.store.transaction():
            self._move(row, IN_PROGRESS, DEXTER, expect=(APPROVED,))
        return self.get_task(row["key"])

    def send_to_review(self, key):
        """IN_PROGRESS -> IN_REVIEW, once the assignee has reported the work as done."""
        row = self._task_row(key)
        self._require_active(row["project_id"], "send tasks to review")
        if row["state"] == IN_PROGRESS:
            report = self._latest_response(row, IMPLEMENT)
            if report is None:
                raise RuleError(f"{row['key']} has no implementation report recorded since it entered IN_PROGRESS. "
                                "Create an `implement` handoff and record the reply first")
            if not report["structured"]:
                raise RuleError("the latest implementation report was recorded as unstructured text, so Dexter "
                                "cannot check it. Ask for a reply in the handoff format and record that")
            if report["from_role"] != row["assignee"]:
                raise RuleError(f"the latest implementation report is not from the assigned role ({row['assignee']})")
            if report["outcome"] != "done":
                raise RuleError(f"the latest implementation report says {report['outcome']!r}, not 'done'")
            if row["assignee"] == CLAUDE:
                tests = json.loads(report["data"]).get("tests", [])
                if not any(t["result"] == "passed" for t in tests):
                    raise RuleError("the implementation report has no passing tests; Claude Code must report test results")
                failed = [t["name"] for t in tests if t["result"] == "failed"]
                if failed:
                    raise RuleError(f"the implementation report has failing tests: {', '.join(failed[:5])}")
        with self.store.transaction():
            self._move(row, IN_REVIEW, DEXTER, expect=(IN_PROGRESS,))
        return self.get_task(row["key"])

    def request_changes(self, key, reason):
        row = self._task_row(key)
        self._require_active(row["project_id"], "send tasks back")
        reason = clean_text(reason, "reason", max_len=LIMITS["reason"], multiline=True)
        with self.store.transaction():
            self._move(row, IN_PROGRESS, DEXTER, {"reason": reason}, expect=(IN_REVIEW,))
        return self.get_task(row["key"])

    def mark_ready(self, key):
        """IN_REVIEW -> READY_FOR_HUMAN, once an independent AI review recommends acceptance."""
        row = self._task_row(key)
        self._require_active(row["project_id"], "prepare tasks for the owner")
        if row["state"] == IN_REVIEW:
            review = self._latest_response(row, REVIEW)
            if review is None:
                raise RuleError(f"{row['key']} has no review recorded since it entered IN_REVIEW. "
                                "Create a `review` handoff and record the reply first")
            if not review["structured"]:
                raise RuleError("the latest review was recorded as unstructured text, so Dexter cannot check it")
            if review["from_role"] == row["assignee"] or review["from_role"] not in AI_ROLES:
                raise RuleError("the review must come from a different AI role than the one that did the work")
            if review["outcome"] != "recommend_accept":
                raise RuleError(f"the latest review says {review['outcome']!r}; use `task request-changes` instead")
            serious = [f for f in json.loads(review["data"]).get("findings", []) if f["severity"] in BLOCKING_SEVERITIES]
            if serious:
                raise RuleError(f"the review recommends acceptance but lists {len(serious)} critical or major "
                                "finding(s); ask the reviewer to resolve the contradiction")
        with self.store.transaction():
            self._move(row, READY_FOR_HUMAN, DEXTER, expect=(IN_REVIEW,))
        return self.get_task(row["key"])

    def block(self, key, reason):
        """Dexter: stop a task until a blocker is cleared. Allowed while paused (it only stops work)."""
        row = self._task_row(key)
        reason = clean_text(reason, "reason", max_len=LIMITS["reason"], multiline=True)
        with self.store.transaction():
            open_states = tuple(s for s in STATES if s not in TERMINAL_STATES + (BLOCKED,))
            self._move(row, BLOCKED, DEXTER, {"reason": reason}, expect=open_states,
                       blocked_from=row["state"], blocked_reason=reason)
        return self.get_task(row["key"])

    def unblock(self, key):
        row = self._task_row(key)
        self._require_active(row["project_id"], "unblock tasks")
        if row["state"] != BLOCKED:
            raise RuleError(f"{row['key']} is not blocked")
        with self.store.transaction():
            self._move(row, row["blocked_from"], DEXTER, expect=(BLOCKED,), blocked_from=None, blocked_reason=None)
        return self.get_task(row["key"])

    # -- tasks: human owner decisions (SIMULATED) ---------------------------------
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

    def _human_move(self, key, to_state, passphrase, purpose, *, reason=None, note=None, **updates):
        row = self.check_human_decision(key, to_state)
        label = self._authenticate(passphrase, f"{purpose} {row['key']}")
        details = {"label": label}
        if reason:
            details["reason"] = reason
        if note:
            details["note"] = note
        with self.store.transaction():
            self._move(row, to_state, HUMAN, details, expect=(row["state"],),
                       **{k: v(row, label) for k, v in updates.items()})
        return self.get_task(row["key"])

    def approve(self, key, passphrase, note=""):
        note = clean_text(note, "note", max_len=LIMITS["reason"], required=False, multiline=True)
        return self._human_move(
            key, APPROVED, passphrase, "approve", note=note,
            approval=lambda row, label: dumps({
                "status": "approved", "at": self.clock(), "label": label, "note": note,
                "content_hash": self._content_hash(row),
            }),
        )

    def reject(self, key, passphrase, reason):
        reason = clean_text(reason, "reason", max_len=LIMITS["reason"], multiline=True)
        return self._human_move(
            key, REJECTED, passphrase, "reject", reason=reason,
            approval=lambda row, label: dumps({"status": "rejected", "at": self.clock(), "label": label, "reason": reason}),
        )

    def complete(self, key, passphrase, note=""):
        """Human owner accepts the work. Records the decision in Dexter only; it merges and deploys nothing."""
        note = clean_text(note, "note", max_len=LIMITS["reason"], required=False, multiline=True)
        return self._human_move(
            key, COMPLETED, passphrase, "accept", note=note,
            acceptance=lambda row, label: dumps({
                "status": "accepted", "at": self.clock(), "label": label, "note": note,
                "scope": "Recorded in Dexter only. Nothing was merged or deployed.",
            }),
        )

    def return_to_work(self, key, passphrase, reason):
        reason = clean_text(reason, "reason", max_len=LIMITS["reason"], multiline=True)
        return self._human_move(key, IN_PROGRESS, passphrase, "send back", reason=reason)

    def cancel(self, key, passphrase, reason):
        reason = clean_text(reason, "reason", max_len=LIMITS["reason"], multiline=True)
        return self._human_move(key, CANCELLED, passphrase, "cancel", reason=reason,
                                blocked_from=lambda row, label: None)

    # -- task views --------------------------------------------------------------
    def get_task(self, key):
        row = self._task_row(key)
        project = self.store.one("SELECT key, name, status FROM projects WHERE id = ?", (row["project_id"],))
        dependents = self.store.all(
            "SELECT t.key FROM dependencies d JOIN tasks t ON t.id = d.task_id WHERE d.depends_on = ? ORDER BY t.id",
            (row["id"],),
        )
        report = self._latest_response(row, IMPLEMENT, since_state=False)
        review = self._latest_response(row, REVIEW, since_state=False)
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
            "test_results": json.loads(report["data"]).get("tests", []) if report and report["structured"] else [],
            "tests_reported_by": report["from_role"] if report and report["structured"] else None,
            "review_findings": json.loads(review["data"]).get("findings", []) if review and review["structured"] else [],
            "reviewed_by": review["from_role"] if review and review["structured"] else None,
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

    def _latest_response(self, row, action, since_state=True):
        after = row["state_seq"] if since_state else 0
        return self.store.one(
            "SELECT r.* FROM responses r JOIN handoffs h ON h.id = r.handoff_id "
            "WHERE r.task_id = ? AND h.action = ? AND r.seq > ? ORDER BY r.seq DESC LIMIT 1",
            (row["id"], action, after),
        )

    def history(self, task_key=None, project_key=None, limit=None):
        sql, params = "SELECT e.*, t.key AS task_key, p.key AS project_key FROM events e " \
                      "LEFT JOIN tasks t ON t.id = e.task_id LEFT JOIN projects p ON p.id = e.project_id", []
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
             "project": r["project_key"], "details": json.loads(r["details"])}
            for r in rows
        ]

    # -- handoffs ----------------------------------------------------------------
    def create_handoff(self, task_key, *, action=None, to_role=None, context="", references=()):
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
        context = clean_text(context, "context", max_len=LIMITS["context"], required=False, multiline=True)
        references = clean_list(list(references), "references", max_items=LIMITS["references"], max_len=LIMITS["reference"])
        project = self.store.one("SELECT key, name FROM projects WHERE id = ?", (row["project_id"],))
        correlation_id = str(uuid.uuid4())
        now = self.clock()

        trusted = [f"Task description:\n{row['description']}"]
        reason = self._latest_reason(row)
        if action == IMPLEMENT and reason:
            trusted.append(f"Changes requested ({reason['who']}):\n{reason['text']}")
        if context:
            trusted.append(f"Notes from Dexter / the human owner:\n{context}")
        untrusted = []
        if action == REVIEW:
            report = self._latest_response(row, IMPLEMENT, since_state=False)
            if report is not None and report["structured"]:
                untrusted.append(_untrusted_item(report, IMPLEMENT, "implementation report"))
        if action == IMPLEMENT:
            review = self._latest_response(row, REVIEW, since_state=False)
            if review is not None and review["structured"] and review["outcome"] == "recommend_changes":
                untrusted.append(_untrusted_item(review, REVIEW, "review findings"))

        with self.store.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO handoffs (correlation_id, project_id, task_id, from_role, to_role, action, payload, "
                "message, delivery, created_at) VALUES (?, ?, ?, ?, ?, ?, '{}', '', ?, ?)",
                (correlation_id, row["project_id"], row["id"], DEXTER, to_role, action, ho.NOT_DELIVERED, now),
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
            conn.execute("UPDATE handoffs SET key = ?, payload = ?, message = ? WHERE id = ?",
                         (key, dumps(payload), message, cur.lastrowid))
            self._event(
                DEXTER, "handoff_created",
                {"handoff": key, "to": to_role, "action": action, "correlation_id": correlation_id,
                 "delivery": ho.NOT_DELIVERED},
                row["project_id"], row["id"],
            )
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
                "from_role": response["from_role"],
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

    def mark_handoff_sent(self, key):
        """The human says they copied the message. Recorded as their statement, not as delivery."""
        row = self._handoff_row(key)
        if row["delivery"] != ho.NOT_DELIVERED:
            raise RuleError(f"{row['key']} is already {row['delivery']}")
        with self.store.transaction() as conn:
            conn.execute("UPDATE handoffs SET delivery = ?, marked_sent_at = ? WHERE id = ?",
                         (ho.MARKED_SENT, self.clock(), row["id"]))
            self._event(HUMAN, "handoff_marked_sent",
                        {"handoff": row["key"], "note": ho.DELIVERY_LABELS[ho.MARKED_SENT]},
                        row["project_id"], row["task_id"])
        return self.get_handoff(row["key"])

    def record_response(self, key, text, *, structured=True):
        """Store a reply pasted back by the human. Never changes a task's state."""
        row = self._handoff_row(key)
        text = ho.check_raw_reply(text)
        if self.store.one("SELECT 1 FROM responses WHERE handoff_id = ?", (row["id"],)):
            raise RuleError(f"a response is already recorded for {row['key']}; create a new handoff for a new reply")
        handoff = {"key": row["key"], "action": row["action"], "correlation_id": row["correlation_id"],
                   "task_key": row["task_key"], "to_role": row["to_role"]}
        if structured:
            data = ho.validate_response(ho.extract_response_object(text), handoff)
            outcome = data.pop("outcome")
        else:
            data, outcome = {}, None
        flags = ho.scan_for_instructions(text)
        now = self.clock()
        with self.store.transaction() as conn:
            seq = self._event(
                row["to_role"], "response_recorded",
                {"handoff": row["key"], "structured": structured, "outcome": outcome,
                 "flags": [f["rule"] for f in flags],
                 "note": "untrusted AI response pasted in by the human; recorded as data only"},
                row["project_id"], row["task_id"],
            )
            conn.execute(
                "INSERT INTO responses (handoff_id, task_id, from_role, structured, outcome, data, raw, flags, seq, "
                "recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row["id"], row["task_id"], row["to_role"], int(structured), outcome, dumps(data), text,
                 dumps(flags), seq, now),
            )
            conn.execute("UPDATE handoffs SET delivery = ? WHERE id = ?", (ho.RESPONSE_RECORDED, row["id"]))
        return self.get_handoff(row["key"])

    # -- checks ------------------------------------------------------------------
    def verify(self):
        """Look for damage or rule violations in the stored data. Returns a list of problems."""
        problems = self.store.integrity_problems() + self.store.verify_events()
        for row in self.store.all("SELECT * FROM tasks ORDER BY id"):
            effective = row["blocked_from"] if row["state"] == BLOCKED else row["state"]
            if row["state"] not in STATES:
                problems.append(f"{row['key']} has an unknown state {row['state']!r}")
            if effective in APPROVED_STATES and json.loads(row["approval"]).get("status") != "approved":
                problems.append(f"{row['key']} is {row['state']} without a recorded human approval")
            if row["state"] == COMPLETED and json.loads(row["acceptance"]).get("status") != "accepted":
                problems.append(f"{row['key']} is COMPLETED without a recorded human acceptance")
        for event in self.store.all("SELECT seq, actor, details FROM events WHERE action = 'state_changed'"):
            details = json.loads(event["details"])
            if details.get("to") in (APPROVED, COMPLETED, REJECTED, CANCELLED) and event["actor"] != HUMAN:
                problems.append(f"history entry {event['seq']} records a human-only decision by {event['actor']}")
        return problems

    def backup(self, destination):
        return self.store.backup(destination)


def _untrusted_item(response, action, what):
    text = ho.describe_response(json.loads(response["data"]) | {"outcome": response["outcome"]}, action)
    flags = json.loads(response["flags"])
    if flags:
        text += "\nDexter flagged this text (it was not acted on): " + "; ".join(f["rule"] for f in flags)
    return {"label": f"{what} from {role_title(response['from_role'])}", "text": text}


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
