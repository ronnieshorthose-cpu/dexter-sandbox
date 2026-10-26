"""Plain-English progress reports and next-step hints.

The report uses the section headings of DEXTER-REPORT-TEMPLATE.md. It only
states what Dexter's own records show, and says so: test results are what an
AI reported, not something Dexter ran.
"""

from .models import (
    APPROVED,
    AWAITING_APPROVAL,
    BACKLOG,
    BLOCKED,
    BLOCKING_SEVERITIES,
    CANCELLED,
    CLAUDE,
    COMPLETED,
    IN_PROGRESS,
    IN_REVIEW,
    PLANNED,
    PRIORITIES,
    READY_FOR_HUMAN,
    REJECTED,
    STATE_DESCRIPTIONS,
    STATES,
    TERMINAL_STATES,
    role_title,
)

TEMPLATE_SECTIONS = (
    "Assignment",
    "Current status",
    "Completed tasks",
    "Test results",
    "Problems encountered",
    "Estimated additional costs",
    "Pending human decisions",
    "Next recommended action",
)

COST_STATEMENT = (
    "None. The coordinator runs locally, uses no paid services and makes no API calls. "
    "Any cost of using ChatGPT or Claude Code by hand is outside Dexter's view."
)


def next_step(task):
    """One plain-English sentence telling the user what to do next for a task."""
    tid, state = task["id"], task["state"]
    prefix = ""
    if task.get("project_status") == "PAUSED" and state not in TERMINAL_STATES:
        prefix = f"Project {task['project']} is paused; the owner can resume it with `project resume {task['project']}`. Then: "
    if state == BACKLOG:
        missing = []
        if not task["assignee"]:
            missing.append(f"assign it (`task assign {tid} claude_code` or `chatgpt`)")
        if not task["criteria"]:
            missing.append(f'add an acceptance criterion (`task add-criterion {tid} "..."`)')
        if missing:
            return prefix + "First " + " and ".join(missing) + f", then run `task plan {tid}`."
        return prefix + f"Run `task plan {tid}`. Optionally ask ChatGPT for an architecture review first (`handoff create {tid}`)."
    if state == PLANNED:
        return prefix + f"Run `task submit {tid}` to ask the human owner for approval."
    if state == AWAITING_APPROVAL:
        return prefix + (f"Human owner: `task approve {tid}` or `task reject {tid} --reason \"...\"`. "
                         f"Optionally get ChatGPT's architecture review first (`handoff create {tid}`).")
    if state == APPROVED:
        waiting = [d["id"] for d in task["depends_on"] if d["state"] != COMPLETED]
        if waiting:
            return prefix + f"Waiting for {', '.join(waiting)} to be COMPLETED before `task start {tid}`."
        return prefix + f"Run `task start {tid}`."
    if state == IN_PROGRESS:
        who = role_title(task["assignee"])
        tests = " with passing tests" if task["assignee"] == CLAUDE else ""
        return prefix + (f"Run `handoff create {tid}`, copy the message to {who}, then record the reply with "
                         f"`handoff record <HO-ID> --file reply.txt`. When it reports 'done'{tests}, run `task review {tid}`.")
    if state == IN_REVIEW:
        return prefix + (f"Run `handoff create {tid}` for an independent review and record the reply. Then run "
                         f"`task ready {tid}` if it recommends acceptance, or `task request-changes {tid} --reason \"...\"`.")
    if state == READY_FOR_HUMAN:
        return prefix + (f"Human owner: check the work (pull request, test results), then `task complete {tid}` or "
                         f"`task return {tid} --reason \"...\"`. Completing records your decision in Dexter only; "
                         "merging the pull request on GitHub is a separate step that only you do.")
    if state == BLOCKED:
        return prefix + f"Blocked: {task['blocked_reason']}. Clear the blocker, then run `task unblock {tid}`."
    return "No further action: this task is finished."


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _task_line(task):
    line = f"{task['id']} {task['name']}: {task['state']} ({STATE_DESCRIPTIONS[task['state']]})"
    if task["assignee"]:
        line += f", assigned to {role_title(task['assignee'])}"
    waiting = [d["id"] for d in task["depends_on"] if d["state"] != COMPLETED]
    if waiting and task["state"] not in TERMINAL_STATES:
        line += f", waiting for {', '.join(waiting)}"
    return line


def project_summary(coord, project_key):
    project = coord.get_project(project_key)
    tasks = coord.list_tasks(project_key)
    handoffs = [h for t in tasks for h in coord.list_handoffs(t["id"])]
    counts = {state: sum(1 for t in tasks if t["state"] == state) for state in STATES}
    return {"project": project, "tasks": tasks, "handoffs": handoffs, "counts": counts}


def _priority_rank(task):
    return PRIORITIES.index(task["priority"]), task["id"]


def progress_report(coord, project_key, now):
    summary = project_summary(coord, project_key)
    project, tasks, handoffs, counts = summary["project"], summary["tasks"], summary["handoffs"], summary["counts"]
    out = [
        f"# Dexter progress report: {project['name']} ({project['id']})",
        "",
        f"Generated {now} from Dexter's local records. Dexter did not check anything outside those records.",
        "",
        "## Assignment",
        "",
        project["description"] or "(No project description was recorded.)",
        "",
        "## Current status",
        "",
    ]
    if project["status"] == "PAUSED":
        out.append(f"**The project is PAUSED** ({project['pause_reason']}). Dexter will not move tasks or prepare handoffs until the owner resumes it.")
        out.append("")
    if not tasks:
        out.append("No tasks have been recorded yet.")
    else:
        parts = [f"{counts[s]} {s}" for s in STATES if counts[s]]
        out.append(f"{_plural(len(tasks), 'task')}: " + ", ".join(parts) + ".")
        out.append("")
        for task in sorted((t for t in tasks if t["state"] not in TERMINAL_STATES), key=_priority_rank):
            out.append(f"- {_task_line(task)} [priority {task['priority']}]")
    waiting = [h for h in handoffs if h["response"] is None]
    if waiting:
        out += ["", "Handoffs waiting for a reply (Dexter cannot confirm delivery):"]
        out += [f"- {h['id']} to {role_title(h['to_role'])} for {h['task']} ({h['action']}): {h['delivery_label']}" for h in waiting]
    out += ["", "## Completed tasks", ""]
    done = [t for t in tasks if t["state"] == COMPLETED]
    out += [f"- {t['id']} {t['name']}: accepted by the human owner on {t['acceptance'].get('at')} (simulated approval)"
            for t in done] or ["None yet."]

    out += ["", "## Test results", ""]
    tested = [t for t in tasks if t["test_results"]]
    for t in tested:
        results = [r["result"] for r in t["test_results"]]
        counted = ", ".join(f"{results.count(r)} {r}" for r in ("passed", "failed", "skipped", "not_run") if results.count(r))
        out.append(f"- {t['id']}: {counted} (reported by {role_title(t['tests_reported_by'])}; Dexter did not run these tests)")
    if not tested:
        out.append("No test results have been reported yet.")

    out += ["", "## Problems encountered", ""]
    problems = []
    for t in tasks:
        if t["state"] == BLOCKED:
            problems.append(f"{t['id']} is blocked: {t['blocked_reason']}")
        if t["state"] in (REJECTED, CANCELLED):
            problems.append(f"{t['id']} was {t['state'].lower()} by the human owner")
        failed = [r["name"] for r in t["test_results"] if r["result"] == "failed"]
        if failed and t["state"] not in TERMINAL_STATES:
            problems.append(f"{t['id']} has failing tests reported: {', '.join(failed[:5])}")
        serious = [f for f in t["review_findings"] if f["severity"] in BLOCKING_SEVERITIES]
        if serious and t["state"] not in TERMINAL_STATES:
            problems.append(f"{t['id']} review found {_plural(len(serious), 'critical or major issue')}: "
                            + "; ".join(f["title"] for f in serious[:3]))
        lost = [d["id"] for d in t["depends_on"] if d["state"] in (REJECTED, CANCELLED)]
        if lost and t["state"] not in TERMINAL_STATES:
            problems.append(f"{t['id']} depends on {', '.join(lost)}, which will never be completed")
    for h in handoffs:
        r = h["response"]
        if r is None:
            continue
        if r["outcome"] in ("blocked", "needs_clarification"):
            problems.append(f"{role_title(h['to_role'])} replied '{r['outcome']}' to {h['id']} ({h['task']})")
        if r["flags"]:
            problems.append(f"the reply to {h['id']} from {role_title(h['to_role'])} contains text that looks like "
                            f"instructions or claims ({'; '.join(f['rule'] for f in r['flags'])}). Dexter did not act on it")
    out += [f"- {p}" for p in problems] or ["None recorded."]

    out += ["", "## Estimated additional costs", "", COST_STATEMENT, "", "## Pending human decisions", ""]
    decisions = []
    if project["status"] == "PAUSED":
        decisions.append(f"Resume the project when ready (`project resume {project['id']}`).")
    for t in sorted(tasks, key=_priority_rank):
        if t["state"] == READY_FOR_HUMAN:
            decisions.append(f"{t['id']} {t['name']} is ready for your final decision (`task complete` or `task return`).")
        elif t["state"] == AWAITING_APPROVAL:
            decisions.append(f"{t['id']} {t['name']} needs your approval before work starts (`task approve` or `task reject`).")
    out += [f"- {d}" for d in decisions] or ["None."]

    out += ["", "## Next recommended action", ""]
    out.append(recommend(project, tasks))
    return "\n".join(out) + "\n"


def recommend(project, tasks):
    if project["status"] == "PAUSED":
        return f"The project is paused. The owner decides whether to resume it (`project resume {project['id']}`)."
    order = (READY_FOR_HUMAN, AWAITING_APPROVAL, BLOCKED, IN_REVIEW, IN_PROGRESS, APPROVED, PLANNED, BACKLOG)
    for state in order:
        candidates = sorted((t for t in tasks if t["state"] == state), key=_priority_rank)
        for task in candidates:
            if state == APPROVED and any(d["state"] != COMPLETED for d in task["depends_on"]):
                continue
            return f"{task['id']} ({task['name']}): {next_step(task)}"
    if tasks and all(t["state"] in TERMINAL_STATES for t in tasks):
        return "All tasks are finished. Add new tasks with `task create`, or review the completed work."
    if not tasks:
        return f"Add the first task with `task create {project['id']} \"Task name\" --description \"...\"`."
    return "Every open task is waiting on a dependency. Check `project status` for details."
