"""Roles, task states, transition rules and input validation.

Everything here is pure data and pure functions, so the rules can be read and
tested on their own.
"""

import re

# -- Roles -------------------------------------------------------------------

HUMAN = "human_owner"
DEXTER = "dexter"
CHATGPT = "chatgpt"
CLAUDE = "claude_code"

ROLES = {
    HUMAN: {
        "title": "Human Owner",
        "kind": "human",
        "duties": [
            "Defines project goals and priorities.",
            "Approves tasks before implementation.",
            "Approves merges, deployments, and expenses.",
            "Can pause or stop any workflow.",
        ],
    },
    DEXTER: {
        "title": "Dexter (Coordinator)",
        "kind": "coordinator",
        "duties": [
            "Receives and organizes development requests.",
            "Breaks approved requests into actionable tasks.",
            "Assigns tasks to ChatGPT or Claude Code.",
            "Tracks task status, dependencies, and blockers.",
            "Collects responses and review findings.",
            "Produces plain-English progress reports.",
            "Requests human approval at required checkpoints.",
        ],
    },
    CHATGPT: {
        "title": "ChatGPT (Architect and Reviewer)",
        "kind": "ai",
        "duties": [
            "Reviews requirements and proposed architecture.",
            "Identifies risks, defects, and missing functionality.",
            "Reviews Claude Code's implementation reports.",
            "Recommends acceptance or additional changes.",
            "Cannot approve merges or deployments.",
        ],
    },
    CLAUDE: {
        "title": "Claude Code (Developer)",
        "kind": "ai",
        "duties": [
            "Implements approved development tasks.",
            "Creates development branches.",
            "Runs tests.",
            "Documents changes.",
            "Opens pull requests.",
            "Reports completion or blockers to Dexter.",
            "Cannot approve its own work, merges or deployments.",
        ],
    },
}

# Not a role: the actor recorded for actions run at the local command line by
# someone whose identity Dexter has not checked (no passphrase was given).
LOCAL_OPERATOR = "local_operator"

AI_ROLES = (CHATGPT, CLAUDE)
ASSIGNABLE_ROLES = AI_ROLES


def role_title(role):
    return ROLES.get(role, {}).get("title", role)


# -- Task states ---------------------------------------------------------------

BACKLOG = "BACKLOG"
PLANNED = "PLANNED"
AWAITING_APPROVAL = "AWAITING_APPROVAL"
APPROVED = "APPROVED"
IN_PROGRESS = "IN_PROGRESS"
IN_REVIEW = "IN_REVIEW"
READY_FOR_HUMAN = "READY_FOR_HUMAN"
COMPLETED = "COMPLETED"
BLOCKED = "BLOCKED"
REJECTED = "REJECTED"
CANCELLED = "CANCELLED"

STATES = (
    BACKLOG, PLANNED, AWAITING_APPROVAL, APPROVED, IN_PROGRESS, IN_REVIEW,
    READY_FOR_HUMAN, COMPLETED, BLOCKED, REJECTED, CANCELLED,
)
TERMINAL_STATES = (COMPLETED, REJECTED, CANCELLED)
# The task's content (name, description, criteria, assignee, dependencies) can
# only change before it is submitted, so an approval always covers what is built.
EDITABLE_STATES = (BACKLOG, PLANNED)

STATE_DESCRIPTIONS = {
    BACKLOG: "recorded, not yet planned",
    PLANNED: "planned, not yet sent for approval",
    AWAITING_APPROVAL: "waiting for the human owner to approve or reject it",
    APPROVED: "approved by the human owner (simulated), not yet started",
    IN_PROGRESS: "being worked on by the assigned AI",
    IN_REVIEW: "being reviewed by an independent AI role",
    READY_FOR_HUMAN: "waiting for the human owner's final decision",
    COMPLETED: "accepted by the human owner (simulated)",
    BLOCKED: "stopped until a blocker is cleared",
    REJECTED: "rejected by the human owner",
    CANCELLED: "cancelled by the human owner",
}

# (from, to) -> the one role allowed to make that move. AI roles never appear:
# ChatGPT and Claude Code can only send responses, which are recorded as data.
# Moves into BLOCKED and back out of it are handled separately.
TRANSITIONS = {
    (BACKLOG, PLANNED): DEXTER,
    (PLANNED, BACKLOG): DEXTER,
    (PLANNED, AWAITING_APPROVAL): DEXTER,
    (AWAITING_APPROVAL, PLANNED): DEXTER,
    (AWAITING_APPROVAL, APPROVED): HUMAN,
    (AWAITING_APPROVAL, REJECTED): HUMAN,
    (APPROVED, IN_PROGRESS): DEXTER,
    (IN_PROGRESS, IN_REVIEW): DEXTER,
    (IN_REVIEW, IN_PROGRESS): DEXTER,
    (IN_REVIEW, READY_FOR_HUMAN): DEXTER,
    (READY_FOR_HUMAN, COMPLETED): HUMAN,
    (READY_FOR_HUMAN, IN_PROGRESS): HUMAN,
}
for _state in STATES:
    if _state not in TERMINAL_STATES:
        TRANSITIONS[(_state, CANCELLED)] = HUMAN


def allowed_actor(from_state, to_state, previous_state=None):
    """Who may move a task from one state to another, or None if the move is invalid."""
    if to_state == BLOCKED:
        return DEXTER if from_state not in TERMINAL_STATES + (BLOCKED,) else None
    if from_state == BLOCKED and to_state != CANCELLED:
        # Unblocking only restores the open state the task was in before.
        restorable = previous_state not in TERMINAL_STATES + (BLOCKED,)
        return DEXTER if to_state == previous_state and restorable else None
    return TRANSITIONS.get((from_state, to_state))


# -- Priorities and handoff actions ------------------------------------------

PRIORITIES = ("critical", "high", "medium", "low")

ARCHITECTURE_REVIEW = "architecture_review"
IMPLEMENT = "implement"
REVIEW = "review"
ACTIONS = {
    ARCHITECTURE_REVIEW: "Review the requirements and proposed approach before the human approves the task.",
    IMPLEMENT: "Carry out the approved task and report the result.",
    REVIEW: "Independently review the implementation report and recommend acceptance or changes.",
}
# Outcomes a response may report, per requested action.
OUTCOMES = {
    ARCHITECTURE_REVIEW: ("recommend_accept", "recommend_changes", "blocked"),
    IMPLEMENT: ("done", "blocked", "needs_clarification"),
    REVIEW: ("recommend_accept", "recommend_changes", "blocked"),
}
TEST_RESULTS = ("passed", "failed", "skipped", "not_run")
SEVERITIES = ("critical", "major", "minor", "info")
BLOCKING_SEVERITIES = ("critical", "major")


# -- Validation ----------------------------------------------------------------

class DexterError(Exception):
    """Base class for errors that are reported to the user as plain messages."""


class ValidationError(DexterError):
    """Input was rejected. The message is safe to show to the user."""


class NotFound(DexterError):
    """No project, task or handoff has that ID."""


class RuleError(DexterError):
    """The workflow rules do not allow this action now."""


class IntegrityError(DexterError):
    """Stored records disagree with the history or with the owner's signatures."""


# Control characters (except tab and newline in multi-line text), carriage
# returns, and invisible or direction-changing characters. Terminal escape
# sequences start with ESC (\x1b), so they are covered too.
_INVISIBLE = r"​-‏  ‪-‮⁠-⁤⁦-⁩﻿"
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f" + _INVISIBLE + "]")
_CONTROL_SINGLE_LINE = re.compile(r"[\x00-\x1f\x7f-\x9f" + _INVISIBLE + "]")


def clean_text(value, field, *, max_len, required=True, multiline=False):
    """Return a trimmed string, or raise ValidationError."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text")
    value = value.replace("\r\n", "\n").strip()
    if required and not value:
        raise ValidationError(f"{field} must not be empty")
    if len(value) > max_len:
        raise ValidationError(f"{field} is too long ({len(value)} characters; the limit is {max_len})")
    pattern = _CONTROL if multiline else _CONTROL_SINGLE_LINE
    if pattern.search(value):
        raise ValidationError(f"{field} contains control or invisible characters")
    return value


def clean_choice(value, field, choices):
    if not isinstance(value, str) or value not in choices:
        raise ValidationError(f"{field} must be one of: {', '.join(choices)}")
    return value


def clean_list(values, field, *, max_items, max_len):
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValidationError(f"{field} must be a list")
    if len(values) > max_items:
        raise ValidationError(f"{field} has too many items ({len(values)}; the limit is {max_items})")
    return [clean_text(v, f"{field} item", max_len=max_len) for v in values]


def safe_display(text):
    """Remove control and invisible characters before printing stored text.

    AI responses are untrusted; escape sequences in them must not reach the terminal.
    """
    if text is None:
        return ""
    return _CONTROL.sub("", str(text))


LIMITS = {
    "project_name": 80,
    "task_name": 120,
    "description": 10_000,
    "criterion": 500,
    "criteria": 30,
    "reason": 2_000,
    "reference": 500,
    "references": 20,
    "context": 10_000,
}

PROJECT_KEY_RE = re.compile(r"^PRJ-\d{3,}$")
TASK_KEY_RE = re.compile(r"^TASK-\d{4,}$")
HANDOFF_KEY_RE = re.compile(r"^HO-\d{4,}$")
