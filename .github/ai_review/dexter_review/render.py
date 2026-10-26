"""Comment rendering, sanitisation of untrusted text, and the review-state marker."""

import json
import re
from dataclasses import dataclass

BOT_LOGIN = "github-actions[bot]"
STATUS_CONTEXT = "Dexter AI review (advisory)"
MARKER_PREFIX = "<!-- dexter-ai-review:v1 "
_MARKER_RE = re.compile(r"^<!-- dexter-ai-review:v1 (\{[^\n]*\}) -->\n")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

STATES = ("in-progress", "completed", "partial", "failed", "blocked", "dry-run", "nothing-to-review")

HEADLINES = {
    "in-progress": "⏳ Review in progress",
    "completed": "✅ Review completed (advisory)",
    "partial": "⚠️ PARTIAL review — some changes were not reviewed",
    "failed": "❌ Review FAILED — no AI review was performed",
    "blocked": "⛔ Not reviewed — blocked by a safety or spending limit",
    "dry-run": "🧪 DRY RUN — no AI review was performed",
    "nothing-to-review": "ℹ️ Nothing to review — no reviewable changes were sent",
}

BANNER = (
    "> **Automated AI review — advisory only. This is NOT a human approval.**\n"
    "> It cannot approve, merge, or deploy this pull request. "
    "A human must review and approve it."
)


@dataclass(frozen=True)
class Marker:
    sha: str
    state: str
    paid_calls: int


@dataclass(frozen=True)
class BotComment:
    comment_id: int
    marker: Marker


def sanitize(text, limit=2000):
    """Neutralise untrusted text so it renders as plain inline text.

    Removes HTML/comments (so model output cannot forge a state marker), breaks
    @mentions, disables links/images, and collapses newlines so the text cannot
    start headings, quotes, tables, or a fake banner.
    """
    text = str(text)
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"([\\`*_\[\]()!|#~])", r"\\\1", text)
    text = text.replace("@", "@​")
    text = re.sub(r"(?i)\b(https?|ftp)://", r"\1[:]//", text)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def code_span(text, limit=300):
    """Render a path or ID inside a code span that cannot be broken out of."""
    text = re.sub(r"[`\r\n]", "'", str(text))[:limit]
    return f"`` {text} ``"


def render_marker(marker):
    payload = json.dumps(
        {"sha": marker.sha, "state": marker.state, "paid_calls": marker.paid_calls},
        separators=(",", ":"),
    )
    return f"{MARKER_PREFIX}{payload} -->\n"


def parse_marker(body):
    """Parse the marker on the first line. Anything malformed is ignored."""
    if not isinstance(body, str):
        return None
    match = _MARKER_RE.match(body)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    sha, state, calls = data.get("sha"), data.get("state"), data.get("paid_calls")
    if not (isinstance(sha, str) and _SHA_RE.fullmatch(sha)):
        return None
    if state not in STATES:
        return None
    if isinstance(calls, bool) or not isinstance(calls, int) or not 0 <= calls <= 10_000:
        return None
    return Marker(sha=sha, state=state, paid_calls=calls)


def find_bot_comment(comments):
    """Newest marker comment written by the Actions bot. Other authors are ignored."""
    found = None
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        user = comment.get("user") or {}
        if user.get("login") != BOT_LOGIN or user.get("type") != "Bot":
            continue
        marker = parse_marker(comment.get("body"))
        cid = comment.get("id")
        if marker is None or isinstance(cid, bool) or not isinstance(cid, int):
            continue
        if found is None or cid > found.comment_id:
            found = BotComment(comment_id=cid, marker=marker)
    return found


def render_comment(
    marker,
    *,
    model="",
    detail="",
    review=None,
    notes=(),
    bundle=None,
    cap=0,
    usage_line="",
    stale_head="",
    run_url="",
):
    lines = [render_marker(marker).rstrip("\n"), "## 🤖 Dexter AI review", "", BANNER, ""]
    if stale_head:
        lines += [
            f"> ⚠️ **STALE:** this review covers commit `{marker.sha[:12]}`, but the pull "
            f"request is now at `{stale_head[:12]}`. It does not cover the latest changes.",
            "",
        ]
    lines += [
        f"**Result:** {HEADLINES[marker.state]}",
        f"**{'Reviewed commit' if marker.state in ('completed', 'partial') else 'Commit'}:** `{marker.sha}`",
    ]
    if model:
        lines.append(f"**Reviewer model:** {code_span(model, 80)} (OpenAI)")
    lines.append(f"**Paid API calls on this PR:** {marker.paid_calls} of {cap} allowed")
    if usage_line:
        lines.append(f"**Usage:** {usage_line}")
    if run_url:
        lines.append(f"**Workflow run:** {run_url}")
    if detail:
        lines += ["", f"**Details:** {sanitize(detail, 600)}"]

    if review is not None:
        lines += ["", f"### Summary (overall risk: {review['overall_risk']})", "", sanitize(review["summary"])]
        lines += ["", f"### Findings ({len(review['findings'])})", ""]
        if not review["findings"]:
            lines.append("No problems reported.")
        for f in review["findings"]:
            where = code_span(f["file"]) + (f":{f['line']}" if f["line"] else "")
            if not f["location_verified"]:
                where += " _(location not in the reviewed diff)_"
            lines.append(
                f"- **{f['severity'].upper()}** · {f['category']} · {where} — **{sanitize(f['title'], 200)}**"
            )
            lines.append(f"  - {sanitize(f['explanation'])}")
            if f["suggestion"]:
                lines.append(f"  - Suggestion: {sanitize(f['suggestion'])}")
        if review["testing_gaps"]:
            lines += ["", "### Testing gaps", ""] + [f"- {sanitize(g, 500)}" for g in review["testing_gaps"]]
        if review["suspicious_instructions"]:
            lines += ["", "### ⚠️ Suspicious instructions found in the diff", ""]
            lines += [f"- {sanitize(s, 500)}" for s in review["suspicious_instructions"]]
    if notes:
        lines += ["", "### Validation notes", ""] + [f"- {sanitize(n, 200)}" for n in notes]
    if bundle is not None and bundle.omitted:
        lines += ["", "### Not reviewed", ""]
        lines += [f"- {code_span(path)} — {reason}" for path, reason in bundle.omitted[:100]]
        if len(bundle.omitted) > 100:
            lines.append(f"- … and {len(bundle.omitted) - 100} more")
    lines += ["", "---", "_Advisory output from an automated reviewer. Findings may be wrong or incomplete._"]
    return "\n".join(lines) + "\n"
