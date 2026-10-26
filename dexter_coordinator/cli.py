"""Command-line interface: python3 -m dexter_coordinator <command> ...

Run `python3 -m dexter_coordinator --help`, or see docs/COORDINATOR.md.
"""

import argparse
import getpass
import json
import os
import sqlite3
import sys

from . import __version__
from .auth import APPROVAL_LABEL, AuthError
from .coordinator import Coordinator, utc_now
from .handoff import MAX_RESPONSE_CHARS
from .models import (
    ACTIONS,
    AI_ROLES,
    APPROVED,
    CANCELLED,
    CHATGPT,
    CLAUDE,
    COMPLETED,
    DEXTER,
    HUMAN,
    IN_PROGRESS,
    LOCAL_OPERATOR,
    PRIORITIES,
    REJECTED,
    ROLES,
    STATE_DESCRIPTIONS,
    STATES,
    DexterError,
    ValidationError,
    role_title,
    safe_display,
)
from .report import next_step, progress_report, project_summary

DEFAULT_DB = os.path.join("~", ".dexter", "coordinator.db")

# Who made each history entry, and how sure Dexter is about it.
ACTOR_LABELS = {
    HUMAN: "OWNER (passphrase, signed)",
    DEXTER: "DEXTER (operator unverified)",
    LOCAL_OPERATOR: "LOCAL OPERATOR (unverified)",
    CHATGPT: "CHATGPT (claimed)",
    CLAUDE: "CLAUDE CODE (claimed)",
}


def say(text=""):
    """Print text that may contain stored, untrusted content, without control characters."""
    print(safe_display(text))


def read_passphrase(prompt="Owner passphrase: "):
    """Ask for the owner passphrase, typed into an interactive terminal.

    There is no option, environment variable or file for the passphrase, and
    plain pipes are refused. This stops scripts that simply pipe text in. It is
    NOT an authentication boundary: a program that opens a pseudo-terminal can
    type into this prompt. The passphrase itself is the only real check, so it
    must never be given to an AI assistant or stored where one can read it.
    """
    if not sys.stdin.isatty():
        raise AuthError(
            "owner actions need the passphrase typed into an interactive terminal; input from a pipe or "
            "file is refused"
        )
    return getpass.getpass(prompt)


def human_banner(what, details=()):
    say("")
    say("OWNER ACTION (SIMULATED APPROVAL)")
    say(f"You are about to {what}.")
    for line in details:
        say(f"  {line}")
    say(APPROVAL_LABEL)
    say("It is recorded and signed in Dexter only. Nothing on GitHub or anywhere else changes.")


def task_summary(task):
    """What an owner approves: the exact content the approval will cover."""
    lines = [f"Assigned to: {role_title(task['assignee']) if task['assignee'] else '(nobody)'}",
             f"Priority: {task['priority']}",
             "Depends on: " + (", ".join(d["id"] for d in task["depends_on"]) or "(none)"),
             "Description:"]
    lines += [f"  {line}" for line in task["description"].splitlines()]
    lines.append("Acceptance criteria:")
    lines += [f"  {i}. {c}" for i, c in enumerate(task["criteria"], 1)]
    return lines


# -- formatting ------------------------------------------------------------------

def show_task(task):
    say(f"{task['id']}  {task['name']}")
    say(f"  State:        {task['state']} ({STATE_DESCRIPTIONS[task['state']]})")
    if task["state"] == "BLOCKED":
        say(f"  Blocked:      {task['blocked_reason']} (was {task['blocked_from']})")
    say(f"  Project:      {task['project']} {task['project_name']}" + ("  [PAUSED]" if task["project_status"] == "PAUSED" else ""))
    say(f"  Priority:     {task['priority']}")
    say(f"  Assigned to:  {role_title(task['assignee']) if task['assignee'] else '(nobody yet)'}")
    deps = ", ".join(f"{d['id']} ({d['state']})" for d in task["depends_on"]) or "(none)"
    say(f"  Depends on:   {deps}")
    if task["dependents"]:
        say(f"  Needed by:    {', '.join(task['dependents'])}")
    say(f"  Approval:     {describe_approval(task['approval'])}")
    if task["acceptance"].get("status") == "accepted":
        say(f"  Acceptance:   accepted {task['acceptance']['at']} (SIMULATED approval; nothing merged or deployed)")
    say("  Description:")
    for line in task["description"].splitlines():
        say(f"    {line}")
    say("  Acceptance criteria:")
    for i, criterion in enumerate(task["criteria"], 1):
        say(f"    {i}. {criterion}")
    if not task["criteria"]:
        say("    (none yet)")
    if task["test_results"]:
        say(f"  Test results (from a reply that claims to be from {role_title(task['tests_claimed_by'])}, "
            "recorded by the owner; Dexter did not run them):")
        for t in task["test_results"]:
            say(f"    {t['result']:<8} {t['name']}")
    if task["review_claimed_by"]:
        say(f"  Latest review (advisory, from a reply that claims to be from {role_title(task['review_claimed_by'])}; "
            f"the author is not verified): {task['review_outcome']}")
        for f in task["review_findings"]:
            say(f"    [{f['severity']}] {f['title']}" + (f" ({f['file']})" if f["file"] else ""))
    say(f"  Next step:    {next_step(task)}")


def describe_approval(approval):
    status = approval.get("status")
    if status == "approved":
        return f"APPROVED {approval['at']} (SIMULATED human approval)"
    if status == "rejected":
        return f"REJECTED {approval['at']}: {approval.get('reason', '')}"
    if status == "awaiting":
        return "waiting for the human owner"
    return "not requested yet"


def show_handoff(h, *, full=True):
    say(f"{h['id']}  {h['action']} for {h['task']}, to {role_title(h['to_role'])}")
    say(f"  Correlation ID: {h['correlation_id']}")
    say(f"  Created:        {h['created_at']}")
    say(f"  Delivery:       {h['delivery_label']}")
    r = h["response"]
    if r is None:
        say("  Response:       none recorded")
    else:
        kind = "structured" if r["structured"] else "UNSTRUCTURED (cannot be checked; it does not count as evidence)"
        say(f"  Response:       recorded by the owner {r['recorded_at']}, {kind}")
        say(f"  Claimed sender: {role_title(r['claimed_from'])} (not verified: anyone can write a reply)")
        if r["structured"]:
            say(f"  Outcome:        {r['outcome']}")
            say(f"  Summary:        {r['data']['summary']}")
        show_flags(r["flags"])
    if full:
        say("")
        say("----- COPY EVERYTHING BELOW THIS LINE -----")
        say(h["message"].rstrip("\n"))
        say("----- COPY EVERYTHING ABOVE THIS LINE -----")


def show_flags(flags):
    if not flags:
        return
    say("  WARNING: the reply contains text that looks like instructions or claims.")
    say("  Dexter recorded it as data and did NOT act on it. It may be harmless; read it yourself:")
    for f in flags:
        say(f"    - {f['rule']}: \"...{f['excerpt']}...\"")


def describe_event(e):
    d = e["details"]
    action, task = e["action"], e["task"] or ""
    if action == "state_changed":
        text = f"{task}: {d['from']} -> {d['to']}"
        if d.get("reason"):
            text += f" (reason: {d['reason']})"
        if d.get("note"):
            text += f" (note: {d['note']})"
        if d.get("label"):
            text += " [SIMULATED human approval]"
        return text
    if action == "project_created":
        return f"created project {d['project']} \"{d['name']}\""
    if action == "task_created":
        return f"created {d['task']} \"{d['name']}\""
    if action == "handoff_created":
        return f"prepared {d['handoff']} ({d['action']}) for {role_title(d['to'])}; not delivered (manual copy needed)"
    if action == "handoff_marked_sent":
        return f"confirmed {d['handoff']} was copied to the assistant (not confirmed by any delivery mechanism)"
    if action == "response_recorded":
        text = (f"recorded the reply to {d['handoff']} (claims to be from {role_title(d.get('claimed_from'))}), "
                f"outcome {d['outcome'] or 'unstructured'}")
        if d.get("flags"):
            text += f"; flagged: {', '.join(d['flags'])}"
        return text
    if action == "owner_passphrase_set":
        return "set up the owner passphrase (whoever knows it is treated as the owner)"
    if action == "owner_passphrase_changed":
        return "changed the owner passphrase"
    if action == "owner_check_failed":
        wait = f"; owner actions paused for {d['wait_seconds']}s" if d.get("wait_seconds") else ""
        return f"wrong owner passphrase (tried to {d.get('purpose', 'act as the owner')}){wait}"
    if action == "project_paused":
        return f"paused the project: {d.get('reason', '')}"
    if action == "project_resumed":
        return "resumed the project [SIMULATED human approval]"
    if action == "task_assigned":
        return f"{task} assigned to {role_title(d['assignee'])}"
    if action in ("dependency_added", "dependency_removed"):
        return f"{task} {'now depends' if action == 'dependency_added' else 'no longer depends'} on {d['depends_on']}"
    if action == "criterion_added":
        return f"{task}: added acceptance criterion \"{d['criterion']}\""
    if action == "task_edited":
        return f"{task}: edited {', '.join(d['fields'])}"
    return f"{action} {json.dumps(d, ensure_ascii=True)}"


def show_history(events):
    if not events:
        say("No history yet.")
    for e in events:
        say(f"{e['ts']}  #{e['seq']:<4} {ACTOR_LABELS.get(e['actor'], e['actor']):<29} {describe_event(e)}")


# -- commands --------------------------------------------------------------------

def cmd_init(c, args):
    if c.owner_is_set():
        say(f"Dexter is already set up at {c.store.path}.")
        return 0
    say("Set the owner passphrase (at least 12 characters). You will type it for every owner action:")
    say("approve, reject, complete, return, cancel, resume, recording a reply and marking a handoff sent.")
    say("Whoever knows this passphrase is the owner as far as Dexter can tell. Never give it to an AI")
    say("assistant, and keep this database where AI tools cannot read it (see docs/COORDINATOR-SECURITY.md).")
    say(APPROVAL_LABEL)
    first = read_passphrase("New owner passphrase: ")
    second = read_passphrase("Type it again: ")
    if first != second:
        raise ValidationError("the two passphrases do not match; nothing was saved")
    c.set_owner_passphrase(first)
    say(f"Done. Dexter's database is {c.store.path}")
    return 0


def cmd_owner_change(c, args):
    old = read_passphrase("Current owner passphrase: ")
    new = read_passphrase("New owner passphrase: ")
    if new != read_passphrase("Type the new one again: "):
        raise ValidationError("the two passphrases do not match; nothing was changed")
    c.change_owner_passphrase(old, new)
    say("Owner passphrase changed.")
    return 0


def cmd_roles(c, args):
    for role, info in ROLES.items():
        say(f"{info['title']}  [{role}]")
        for duty in info["duties"]:
            say(f"  - {duty}")
        say("")
    return 0


def cmd_project_create(c, args):
    p = c.create_project(args.name, args.description or "")
    say(f"Created project {p['id']} \"{p['name']}\".")
    say(f"Next: add a task with `task create {p['id']} \"Task name\" --description \"...\"`.")
    return 0


def cmd_project_list(c, args):
    projects = c.list_projects()
    if not projects:
        say("No projects yet. Create one with `project create \"Name\"`.")
    for p in projects:
        say(f"{p['id']}  {p['name']}  [{p['status']}]")
    return 0


def cmd_project_status(c, args):
    s = project_summary(c, args.project)
    p = s["project"]
    say(f"{p['id']}  {p['name']}  [{p['status']}]" + (f"  paused: {p['pause_reason']}" if p["pause_reason"] else ""))
    if p["description"]:
        say(f"  {p['description']}")
    say("")
    if not s["tasks"]:
        say("No tasks yet.")
    for t in s["tasks"]:
        waiting = [d["id"] for d in t["depends_on"] if d["state"] != COMPLETED]
        extra = f"  waiting for {', '.join(waiting)}" if waiting and t["state"] not in (COMPLETED, REJECTED, CANCELLED) else ""
        who = role_title(t["assignee"]) if t["assignee"] else "unassigned"
        say(f"{t['id']}  {t['state']:<18} {t['priority']:<8} {who:<34} {t['name']}{extra}")
    open_handoffs = [h for h in s["handoffs"] if h["response"] is None]
    if open_handoffs:
        say("")
        say("Handoffs waiting for a reply:")
        for h in open_handoffs:
            say(f"  {h['id']} ({h['action']}, {h['task']}) to {role_title(h['to_role'])}: {h['delivery_label']}")
    return 0


def cmd_project_pause(c, args):
    p = c.pause_project(args.project, args.reason)
    say(f"Paused {p['id']}. Dexter will not move tasks or prepare handoffs until the owner resumes it.")
    return 0


def cmd_project_resume(c, args):
    p = c.get_project(args.project)
    human_banner(f"resume project {p['id']} \"{p['name']}\"")
    c.resume_project(p["id"], read_passphrase())
    say(f"Resumed {p['id']}.")
    return 0


def cmd_task_create(c, args):
    t = c.create_task(
        args.project, args.name, args.description, priority=args.priority,
        criteria=args.criterion or [], depends_on=args.depends_on or [], assignee=args.assign,
    )
    say(f"Created {t['id']} \"{t['name']}\" in BACKLOG.")
    say(f"Next: {next_step(t)}")
    return 0


def cmd_task_list(c, args):
    tasks = c.list_tasks(args.project, args.state)
    if not tasks:
        say("No tasks found.")
    for t in tasks:
        who = role_title(t["assignee"]) if t["assignee"] else "unassigned"
        say(f"{t['id']}  {t['project']}  {t['state']:<18} {t['priority']:<8} {who:<34} {t['name']}")
    return 0


def cmd_task_show(c, args):
    show_task(c.get_task(args.task))
    return 0


def cmd_task_history(c, args):
    show_history(c.history(task_key=args.task))
    return 0


def _after(c, task_id, message):
    t = c.get_task(task_id)
    say(message.format(**t))
    say(f"Next: {next_step(t)}")
    return 0


def cmd_task_edit(c, args):
    c.edit_task(args.task, name=args.name, description=args.description, priority=args.priority)
    return _after(c, args.task, "Updated {id}.")


def cmd_task_add_criterion(c, args):
    c.add_criterion(args.task, args.text)
    return _after(c, args.task, "Added an acceptance criterion to {id}.")


def cmd_task_assign(c, args):
    c.assign(args.task, args.role)
    return _after(c, args.task, "Assigned {id}.")


def cmd_task_depend(c, args):
    c.add_dependency(args.task, args.depends_on)
    return _after(c, args.task, "{id} now depends on " + args.depends_on.upper() + ".")


def cmd_task_undepend(c, args):
    c.remove_dependency(args.task, args.depends_on)
    return _after(c, args.task, "{id} no longer depends on " + args.depends_on.upper() + ".")


def _dexter_step(method, message):
    def run(c, args):
        getattr(c, method)(args.task)
        return _after(c, args.task, message)
    return run


def cmd_task_request_changes(c, args):
    c.request_changes(args.task, args.reason)
    return _after(c, args.task, "{id} is back IN_PROGRESS for changes.")


def cmd_task_block(c, args):
    c.block(args.task, args.reason)
    return _after(c, args.task, "{id} is BLOCKED.")


def _human_step(method, to_state, verb, needs_reason=False):
    def run(c, args):
        task = c.get_task(args.task)
        c.check_human_decision(task["id"], to_state)  # refuse before asking for the passphrase
        details = task_summary(task) if to_state == APPROVED else ()
        human_banner(f"{verb} {task['id']} \"{task['name']}\"", details)
        if needs_reason:
            getattr(c, method)(task["id"], read_passphrase(), args.reason)
        else:
            getattr(c, method)(task["id"], read_passphrase(), args.note or "")
        return _after(c, task["id"], "{id} is now {state}.")
    return run


def cmd_handoff_create(c, args):
    h = c.create_handoff(args.task, action=args.action, to_role=args.to, context=args.context or "",
                         references=args.ref or [])
    if args.save:
        _write_new_file(args.save, h["message"])
        say(f"Saved the message to {args.save}.")
    show_handoff(h, full=not args.save)
    say("")
    say(f"Dexter has NOT sent this anywhere. Copy it into {role_title(h['to_role'])} yourself.")
    say("Read it first: anyone who can run Dexter's command line can add text to a handoff.")
    say(f"Optional: `handoff mark-sent {h['id']}` once you have pasted it (asks for the owner passphrase).")
    say(f"Then save the reply to a file and run `handoff record {h['id']} --file reply.txt` (passphrase).")
    return 0


def cmd_handoff_show(c, args):
    h = c.get_handoff(args.handoff)
    if args.json:
        print(json.dumps(h["payload"], indent=2, ensure_ascii=True))
        return 0
    show_handoff(h, full=True)
    if args.raw and h["response"]:
        say("")
        say("----- RECORDED REPLY (untrusted data; control characters removed) -----")
        say(h["response"]["raw"])
    return 0


def cmd_handoff_list(c, args):
    handoffs = c.list_handoffs(args.task)
    if not handoffs:
        say("No handoffs yet.")
    for h in handoffs:
        if h["response"] is None:
            state = h["delivery"]
        else:
            state = h["response"]["outcome"] or "unstructured reply"
        say(f"{h['id']}  {h['task']}  {h['action']:<20} to {role_title(h['to_role']):<34} {state}")
    return 0


def cmd_handoff_mark_sent(c, args):
    h = c.get_handoff(args.handoff)
    if h["delivery"] != "NOT_DELIVERED":
        say(f"{h['id']}: {h['delivery_label']}")
        return 0
    human_banner(f"confirm that you copied {h['id']} to {role_title(h['to_role'])}")
    h = c.mark_handoff_sent(h["id"], read_passphrase())
    say(f"{h['id']}: {h['delivery_label']}")
    return 0


def cmd_handoff_record(c, args):
    c.get_handoff(args.handoff)  # report an unknown handoff before reading the file
    text = _read_reply(args.file)
    preview = c.preview_response(args.handoff, text, structured=not args.unstructured)
    details = [f"Handoff: {preview['handoff']} for {preview['task']}",
               f"Claimed sender: {role_title(preview['claimed_from'])} (Dexter cannot verify who wrote it)"]
    if preview["structured"]:
        details += [f"Outcome: {preview['outcome']}", f"Summary: {preview['data']['summary']}"]
    else:
        details.append("UNSTRUCTURED: Dexter cannot check it, and it will not count as evidence")
    show_flags(preview["flags"])
    human_banner("record this reply exactly as pasted. Only confirm if you pasted it from that assistant yourself",
                 details)
    h = c.record_response(args.handoff, text, read_passphrase(), structured=not args.unstructured)
    say(f"Recorded the reply to {h['id']}. Recording a reply never changes a task's state or approval.")
    return _after(c, h["task"], "{id} is {state}.")


def cmd_history(c, args):
    show_history(c.history(project_key=args.project, limit=args.limit))
    return 0


def cmd_report(c, args):
    text = progress_report(c, args.project, utc_now())
    if args.save:
        _write_new_file(args.save, text)
        say(f"Saved the report to {args.save}.")
    else:
        say(text.rstrip("\n"))
    return 0


def cmd_check(c, args):
    passphrase = None
    if args.owner:
        human_banner("check every owner signature in the database")
        passphrase = read_passphrase()
    problems = c.verify(passphrase=passphrase)
    warnings = c.permission_warnings()
    for w in warnings:
        say(f"Warning: {w}")
    if problems:
        say(f"Found {len(problems)} problem(s) in {c.store.path}:")
        for p in problems:
            say(f"  - {p}")
        say("See \"Recovering from errors\" in docs/COORDINATOR.md.")
        return 1
    seq, digest = c.anchor()
    checked = "the history, the stored records and every owner signature" if args.owner else \
        "the history and the stored records (add --owner to also check owner signatures)"
    say(f"No problems found in {c.store.path}: checked {checked}.")
    say(f"History anchor: entry {seq}, {digest[:16]}. Note it somewhere outside this computer; if a later check")
    say("shows an older anchor, the file was replaced with an older copy.")
    return 1 if warnings else 0


def cmd_backup(c, args):
    path = c.backup(args.destination)
    say(f"Backed up the database to {path}.")
    return 0


def _read_reply(path):
    if path == "-":
        text = sys.stdin.read(MAX_RESPONSE_CHARS + 1)
    else:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read(MAX_RESPONSE_CHARS + 1)
        except FileNotFoundError:
            raise ValidationError(f"there is no file {path}") from None
        except UnicodeDecodeError:
            raise ValidationError(f"{path} is not UTF-8 text") from None
        except OSError as exc:
            raise ValidationError(f"cannot read {path}: {exc.strerror}") from None
    return text


def _write_new_file(path, text):
    try:
        with open(path, "x", encoding="utf-8") as f:
            f.write(text)
    except FileExistsError:
        raise ValidationError(f"{path} already exists; choose a new file name") from None
    except OSError as exc:
        raise ValidationError(f"cannot write {path}: {exc.strerror}") from None


# -- argument parsing ------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="python3 -m dexter_coordinator",
        description="Dexter project coordinator (Phase 3A). Local only: no network calls, no GitHub actions. "
                    "Human owner decisions are SIMULATED approvals checked with a local passphrase.",
        epilog="Start with `init`, then `project create`. Full guide: docs/COORDINATOR.md",
    )
    parser.add_argument("--db", help=f"database file (default: $DEXTER_DB or {DEFAULT_DB})")
    parser.add_argument("--allow-inside-git-repo", action="store_true",
                        help="allow a database or backup inside a git working tree (not recommended)")
    parser.add_argument("--version", action="version", version=f"Dexter coordinator {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def add(subparsers, name, func, help_text):
        p = subparsers.add_parser(name, help=help_text, description=help_text)
        p.set_defaults(func=func)
        return p

    add(sub, "init", cmd_init, "Set up the database and the owner passphrase (interactive).")
    add(sub, "roles", cmd_roles, "Show what each role (human, Dexter, ChatGPT, Claude Code) does.")

    owner = sub.add_parser("owner", help="Owner passphrase.").add_subparsers(dest="sub", metavar="SUBCOMMAND", required=True)
    add(owner, "change-passphrase", cmd_owner_change, "Change the owner passphrase (interactive).")

    project = sub.add_parser("project", help="Create, list, pause and resume projects.").add_subparsers(
        dest="sub", metavar="SUBCOMMAND", required=True)
    p = add(project, "create", cmd_project_create, "Create a project.")
    p.add_argument("name")
    p.add_argument("--description", help="what the project is for")
    add(project, "list", cmd_project_list, "List projects.")
    p = add(project, "status", cmd_project_status, "Show a project's tasks and open handoffs.")
    p.add_argument("project", help="project ID, e.g. PRJ-001")
    p = add(project, "pause", cmd_project_pause, "Human owner: pause all coordinator activity (no passphrase needed).")
    p.add_argument("project")
    p.add_argument("--reason", required=True)
    p = add(project, "resume", cmd_project_resume, "Human owner: resume a paused project (passphrase).")
    p.add_argument("project")

    task = sub.add_parser("task", help="Create, view and move tasks.").add_subparsers(
        dest="sub", metavar="SUBCOMMAND", required=True)
    p = add(task, "create", cmd_task_create, "Create a task in BACKLOG.")
    p.add_argument("project", help="project ID, e.g. PRJ-001")
    p.add_argument("name")
    p.add_argument("--description", required=True)
    p.add_argument("--priority", choices=PRIORITIES, default="medium")
    p.add_argument("--criterion", action="append", help="an acceptance criterion (repeat for more)")
    p.add_argument("--depends-on", action="append", help="a task ID this task needs first (repeat for more)")
    p.add_argument("--assign", choices=list(AI_ROLES) + ["claude"], help="the AI role that does the work")
    p = add(task, "list", cmd_task_list, "List tasks.")
    p.add_argument("--project")
    p.add_argument("--state", choices=STATES, type=str.upper)
    for name, func, text in (("show", cmd_task_show, "Show a task and its next step."),
                             ("history", cmd_task_history, "Show a task's activity history.")):
        add(task, name, func, text).add_argument("task", help="task ID, e.g. TASK-0001")
    p = add(task, "edit", cmd_task_edit, "Change a task's name, description or priority (BACKLOG or PLANNED only).")
    p.add_argument("task")
    p.add_argument("--name")
    p.add_argument("--description")
    p.add_argument("--priority", choices=PRIORITIES)
    p = add(task, "add-criterion", cmd_task_add_criterion, "Add an acceptance criterion (BACKLOG or PLANNED only).")
    p.add_argument("task")
    p.add_argument("text")
    p = add(task, "assign", cmd_task_assign, "Assign the task to chatgpt or claude_code (BACKLOG or PLANNED only).")
    p.add_argument("task")
    p.add_argument("role", choices=list(AI_ROLES) + ["claude"])
    p = add(task, "depend", cmd_task_depend, "Make a task depend on another (BACKLOG or PLANNED only).")
    p.add_argument("task")
    p.add_argument("depends_on")
    p = add(task, "undepend", cmd_task_undepend, "Remove a dependency (BACKLOG or PLANNED only).")
    p.add_argument("task")
    p.add_argument("depends_on")

    steps = (
        ("plan", "plan", "BACKLOG -> PLANNED.", "{id} is PLANNED."),
        ("unplan", "unplan", "PLANNED -> BACKLOG.", "{id} is back in BACKLOG."),
        ("submit", "submit_for_approval", "PLANNED -> AWAITING_APPROVAL: ask the human owner to approve.",
         "{id} is waiting for the human owner's approval."),
        ("withdraw", "withdraw", "AWAITING_APPROVAL -> PLANNED, to make changes.", "{id} is back in PLANNED."),
        ("start", "start", "APPROVED -> IN_PROGRESS (all dependencies must be COMPLETED).", "{id} is IN_PROGRESS."),
        ("review", "send_to_review", "IN_PROGRESS -> IN_REVIEW (needs a 'done' report from the assignee).",
         "{id} is IN_REVIEW."),
        ("ready", "mark_ready", "IN_REVIEW -> READY_FOR_HUMAN (needs an independent review recommending acceptance).",
         "{id} is READY_FOR_HUMAN."),
        ("unblock", "unblock", "BLOCKED -> the state it was in before.", "{id} is unblocked."),
    )
    for name, method, text, message in steps:
        add(task, name, _dexter_step(method, message), "Dexter: " + text).add_argument("task")
    p = add(task, "request-changes", cmd_task_request_changes, "Dexter: IN_REVIEW -> IN_PROGRESS with a reason.")
    p.add_argument("task")
    p.add_argument("--reason", required=True)
    p = add(task, "block", cmd_task_block, "Dexter: mark a task BLOCKED with a reason.")
    p.add_argument("task")
    p.add_argument("--reason", required=True)

    human = (
        ("approve", "approve", APPROVED, "approve", False, "Human owner: approve a task before work starts (passphrase)."),
        ("reject", "reject", REJECTED, "reject", True, "Human owner: reject a task (passphrase)."),
        ("complete", "complete", COMPLETED, "accept the work on", False,
         "Human owner: accept finished work (passphrase). Records the decision in Dexter only."),
        ("return", "return_to_work", IN_PROGRESS, "send back", True,
         "Human owner: send READY_FOR_HUMAN work back for changes (passphrase)."),
        ("cancel", "cancel", CANCELLED, "cancel", True, "Human owner: cancel a task (passphrase)."),
    )
    for name, method, to_state, verb, needs_reason, text in human:
        p = add(task, name, _human_step(method, to_state, verb, needs_reason), text)
        p.add_argument("task")
        if needs_reason:
            p.add_argument("--reason", required=True)
        else:
            p.add_argument("--note", help="optional note stored with the decision")

    handoff = sub.add_parser("handoff", help="Prepare messages for ChatGPT / Claude Code and record replies.").add_subparsers(
        dest="sub", metavar="SUBCOMMAND", required=True)
    p = add(handoff, "create", cmd_handoff_create, "Prepare a handoff message for the right AI role.")
    p.add_argument("task")
    p.add_argument("--action", choices=list(ACTIONS), help="default: chosen from the task's state")
    p.add_argument("--to", choices=AI_ROLES, help="default: chosen from the action and assignment")
    p.add_argument("--context", help="extra notes for the assistant")
    p.add_argument("--ref", action="append", help="a file, branch or link to include (repeat for more)")
    p.add_argument("--save", metavar="FILE", help="also write the message to a new file")
    p = add(handoff, "show", cmd_handoff_show, "Show a handoff message and its reply.")
    p.add_argument("handoff", help="handoff ID, e.g. HO-0001")
    p.add_argument("--json", action="store_true", help="print the structured handoff as JSON")
    p.add_argument("--raw", action="store_true", help="also print the recorded reply text")
    p = add(handoff, "list", cmd_handoff_list, "List handoffs.")
    p.add_argument("--task")
    p = add(handoff, "mark-sent", cmd_handoff_mark_sent,
            "Owner: confirm you copied a handoff to the assistant (passphrase; not a confirmed delivery).")
    p.add_argument("handoff")
    p = add(handoff, "record", cmd_handoff_record,
            "Owner: record the assistant's reply as untrusted data (passphrase).")
    p.add_argument("handoff")
    p.add_argument("--file", required=True, help="file with the pasted reply, or - to read standard input")
    p.add_argument("--unstructured", action="store_true",
                   help="record a reply that is not in the handoff format (it will not count as evidence)")

    p = add(sub, "history", cmd_history, "Show the activity history.")
    p.add_argument("--project")
    p.add_argument("--limit", type=int, help="only the last N entries")
    p = add(sub, "report", cmd_report, "Write a plain-English progress report for a project.")
    p.add_argument("project")
    p.add_argument("--save", metavar="FILE", help="write the report to a new file instead of the screen")
    p = add(sub, "check", cmd_check, "Check the database for damage, tampering or rule violations.")
    p.add_argument("--owner", action="store_true", help="also check every owner signature (passphrase)")
    p = add(sub, "backup", cmd_backup, "Copy the database to a new file.")
    p.add_argument("destination")
    return parser


def db_path(args):
    return args.db or os.environ.get("DEXTER_DB") or os.path.expanduser(DEFAULT_DB)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        with Coordinator(db_path(args), allow_inside_git=args.allow_inside_git_repo) as coordinator:
            return args.func(coordinator, args)
    except DexterError as exc:
        say(f"Error: {exc}")
        return 1
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc) or "busy" in str(exc):
            say("Error: the database is busy (another Dexter command is using it). Nothing was changed; try again.")
        else:
            say(f"Error: database problem ({exc}). Nothing was changed. Run `check`.")
        return 1
    except sqlite3.Error as exc:
        say(f"Error: database problem ({exc}). Nothing was changed. Run `check`.")
        return 1
    except KeyboardInterrupt:
        say("\nStopped. Each change is saved completely or not at all, so run `check` if unsure.")
        return 130
