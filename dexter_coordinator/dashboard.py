"""The status summary shown on the guided menu's home screen and in the read-only HTML page.

build() only reads public views (projects, tasks, handoffs), so the owner's
passphrase verifier, salt and wrapped signing key can never appear here.
render_html() produces a static page: no scripts, no forms, no links to other
sites, and a Content-Security-Policy that blocks loading anything. Every value
is HTML-escaped, because task names and AI replies are untrusted text.
"""

import html

from .labels import SIMULATED_NOTICE, UNVERIFIED, reply_label
from .models import (
    APPROVED,
    AWAITING_APPROVAL,
    BLOCKED,
    COMPLETED,
    IMPLEMENT,
    IN_PROGRESS,
    IN_REVIEW,
    READY_FOR_HUMAN,
    REVIEW,
    STATES,
    TERMINAL_STATES,
    role_title,
    safe_display,
)

OUTCOME_WORDS = {
    "done": "says the work is done",
    "blocked": "says it is blocked",
    "needs_clarification": "asks for clarification",
    "recommend_accept": "recommends acceptance",
    "recommend_changes": "recommends changes",
}


def _latest_reply(handoffs, action):
    replied = [h for h in handoffs if h["action"] == action and h["response"] is not None]
    return replied[-1] if replied else None


def _reply_phrase(handoff):
    r = handoff["response"]
    words = OUTCOME_WORDS.get(r["outcome"], "sent an unstructured reply")
    return f"{role_title(r['claimed_from']).split(' (')[0]} {words} ({UNVERIFIED})"


def build(coord):
    """Everything the home screen shows, as plain data."""
    data = {"projects": [], "owner": [], "ai": [], "reviews": [], "active": [], "blocked": [], "out_of_date": 0}
    for project in coord.list_projects():
        tasks = coord.list_tasks(project["id"])
        counts = {s: sum(1 for t in tasks if t["state"] == s) for s in STATES}
        data["projects"].append({"project": project, "tasks": tasks, "counts": counts})
        if project["status"] == "PAUSED":
            data["owner"].append({"id": project["id"], "name": project["name"],
                                  "text": f"project is PAUSED ({project['pause_reason']}); resume it when ready"})
        for t in tasks:
            handoffs = coord.list_handoffs(t["id"])
            base = {"id": t["id"], "name": t["name"], "project": t["project"]}
            if t["state"] == AWAITING_APPROVAL:
                data["owner"].append(dict(base, text="approve or reject before work starts"))
            elif t["state"] == READY_FOR_HUMAN:
                data["owner"].append(dict(base, text="final decision: check the work on GitHub, then accept it or send it back"))
            elif t["state"] == IN_REVIEW:
                report = _latest_reply(handoffs, IMPLEMENT)
                text = _reply_phrase(report) if report else "no implementation report recorded"
                data["reviews"].append(dict(base, text=f"{text}; needs an independent review",
                                            label=reply_label(report, t, handoffs) if report else ""))
            elif t["state"] in (APPROVED, IN_PROGRESS):
                waiting = [d["id"] for d in t["depends_on"] if d["state"] != COMPLETED]
                text = f"{t['state']}, {role_title(t['assignee'])}"
                if waiting and t["state"] == APPROVED:
                    text += f", waiting for {', '.join(waiting)}"
                data["active"].append(dict(base, text=text))
            elif t["state"] == BLOCKED:
                data["blocked"].append(dict(base, text=f"blocked: {t['blocked_reason']}"))
            if t["state"] == READY_FOR_HUMAN:
                review = _latest_reply(handoffs, REVIEW)
                if review:
                    data["reviews"].append(dict(base, text=_reply_phrase(review) + "; waiting for your final decision",
                                                label=reply_label(review, t, handoffs)))
            for h in handoffs:
                if h["response"] is not None or t["state"] in TERMINAL_STATES:
                    continue
                if coord.handoff_is_current(h["id"]):
                    sent = "you confirmed copying it" if h["delivery"] != "NOT_DELIVERED" else "copy not confirmed"
                    data["ai"].append({"id": h["id"], "task": t["id"], "name": t["name"],
                                       "text": f"{h['action']} -> {role_title(h['to_role'])}, prepared {h['created_at']} "
                                               f"({sent})"})
                else:
                    data["out_of_date"] += 1
    return data


def _counts_line(counts):
    parts = [f"{n} {state.lower().replace('_', ' ')}" for state, n in counts.items() if n]
    return ", ".join(parts) if parts else "no tasks yet"


def render_text(data, db_path, width=78):
    """The home screen. Plain ASCII so it works in any Windows console."""
    rule = "=" * width
    out = [rule, " DEXTER  -  local project coordinator (Phase 3B)",
           f" Database: {db_path}", ""]
    out += _wrap(SIMULATED_NOTICE, width, indent=" ")
    out.append(rule)
    if not data["projects"]:
        out.append(" No projects yet. Choose [P] Projects to create your first one.")
    for p in data["projects"]:
        project = p["project"]
        out.append(f" PROJECT  {project['id']}  {project['name']}  [{project['status']}]")
        out.append(f"   {_counts_line(p['counts'])}")
    sections = (
        ("WAITING FOR YOU (owner)", data["owner"]),
        ("WAITING FOR AN AI (paste the reply back when you have it)", data["ai"]),
        ("REVIEWS", data["reviews"]),
        ("ACTIVE TASKS", data["active"]),
        ("BLOCKED", data["blocked"]),
    )
    for title, items in sections:
        if not items:
            continue
        out += ["", f" {title}"]
        for i, item in enumerate(items, 1):
            name = f' "{item["name"]}"' if item.get("name") else ""
            out.append(f"   {i}. {item['id']}{name}: {item['text']}")
    if data["out_of_date"]:
        out += ["", f" ({data['out_of_date']} older handoff(s) are out of date because their task moved on; "
                     "no reply is needed for them.)"]
    if not (data["owner"] or data["ai"] or data["reviews"] or data["active"] or data["blocked"]) and data["projects"]:
        out += ["", " Nothing is waiting. Add a task with [T] Tasks."]
    out.append(rule)
    return "\n".join(safe_display(line) for line in out)


def _wrap(text, width, indent=""):
    lines, line = [], indent
    for word in text.split():
        if len(line) + len(word) + 1 > width and line.strip():
            lines.append(line.rstrip())
            line = indent
        line += word + " "
    if line.strip():
        lines.append(line.rstrip())
    return lines


# -- the read-only HTML page -----------------------------------------------------------

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; form-action 'none'; base-uri 'none'"

STYLE = """
:root { --bg: #fbfbf9; --fg: #1d1d1b; --muted: #5d5d58; --line: #d9d8d2; --card: #ffffff;
        --warn-bg: #fff4d6; --warn-fg: #6b4b00; --accent: #2f5d8a; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #18191b; --fg: #e9e8e4; --muted: #a3a29c; --line: #34353a; --card: #212226;
          --warn-bg: #3a2f12; --warn-fg: #f3d58a; --accent: #8db4dd; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
       font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.5rem; margin: 0 0 4px; }
h2 { font-size: 1.1rem; margin: 28px 0 8px; }
.muted { color: var(--muted); font-size: 0.9rem; }
.notice { background: var(--warn-bg); color: var(--warn-fg); border-radius: 8px; padding: 12px 14px; margin: 16px 0; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; margin: 8px 0; }
ul { margin: 0; padding-left: 20px; }
li { margin: 4px 0; overflow-wrap: anywhere; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.92rem; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
code { font-family: ui-monospace, Consolas, monospace; }
.tag { display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 0 8px; font-size: 0.8rem; }
"""


def _e(value):
    return html.escape(safe_display(value), quote=True)


def _items_html(items):
    if not items:
        return '<p class="muted">Nothing here.</p>'
    rows = []
    for item in items:
        name = f" &ldquo;{_e(item['name'])}&rdquo;" if item.get("name") else ""
        label = f'<br><span class="muted">{_e(item["label"])}</span>' if item.get("label") else ""
        rows.append(f"<li><code>{_e(item['id'])}</code>{name}: {_e(item['text'])}{label}</li>")
    return "<ul>" + "".join(rows) + "</ul>"


def render_html(data, db_path, generated_at):
    """A static, read-only snapshot. It has no buttons: every action happens in the Dexter window."""
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="referrer" content="no-referrer">',
        "<title>Dexter Dashboard</title>",
        f"<style>{STYLE}</style></head><body><main>",
        "<h1>Dexter dashboard</h1>",
        f'<p class="muted">Read-only snapshot generated {_e(generated_at)} from {_e(db_path)}. '
        "It does not update by itself: choose [D] in Dexter again to refresh it.</p>",
        f'<div class="notice"><strong>Read-only page.</strong> It has no buttons and sends nothing anywhere. '
        f"Approvals and all other actions happen only in the Dexter window. {_e(SIMULATED_NOTICE)} "
        f"Every AI reply is labelled {_e(UNVERIFIED)} until you check the work yourself.</div>",
    ]
    for title, key in (("Waiting for you (owner)", "owner"),
                       ("Waiting for an AI (paste the reply back when you have it)", "ai"),
                       ("Reviews", "reviews"), ("Active tasks", "active"), ("Blocked", "blocked")):
        parts.append(f"<h2>{_e(title)}</h2>")
        parts.append(f'<div class="card">{_items_html(data[key])}</div>')
    if data["out_of_date"]:
        parts.append(f'<p class="muted">{data["out_of_date"]} older handoff(s) are out of date because their '
                     "task moved on; no reply is needed for them.</p>")
    parts.append("<h2>Projects</h2>")
    if not data["projects"]:
        parts.append('<p class="muted">No projects yet.</p>')
    for p in data["projects"]:
        project = p["project"]
        parts.append(f'<div class="card"><strong><code>{_e(project["id"])}</code> {_e(project["name"])}</strong> '
                     f'<span class="tag">{_e(project["status"])}</span>')
        if project["description"]:
            parts.append(f'<p class="muted">{_e(project["description"])}</p>')
        if p["tasks"]:
            parts.append('<div class="table-wrap"><table><thead><tr><th>Task</th><th>Name</th><th>State</th>'
                         "<th>Assigned to</th><th>Priority</th></tr></thead><tbody>")
            for t in p["tasks"]:
                who = role_title(t["assignee"]) if t["assignee"] else "unassigned"
                parts.append(f"<tr><td><code>{_e(t['id'])}</code></td><td>{_e(t['name'])}</td>"
                             f"<td>{_e(t['state'])}</td><td>{_e(who)}</td><td>{_e(t['priority'])}</td></tr>")
            parts.append("</tbody></table></div>")
        else:
            parts.append('<p class="muted">No tasks yet.</p>')
        parts.append("</div>")
    parts.append("</main></body></html>")
    return "\n".join(parts) + "\n"
