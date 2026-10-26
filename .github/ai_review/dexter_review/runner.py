"""Orchestrates one reviewer run. All I/O goes through injected clients."""

import json
import re
from dataclasses import dataclass, field

from . import config as config_mod
from .clients import SHA_RE, HTTPError, OpenAIError
from .diffing import prepare_diff
from .render import STATUS_CONTEXT, Marker, find_bot_comment, render_comment
from .review import (
    ReviewValidationError,
    build_payload,
    estimate_max_cost_usd,
    extract_output_text,
    usage_cost_usd,
    validate_review,
)

# Re-running these states on the same commit would only repeat the same paid call.
NO_REPEAT_STATES = ("completed", "partial", "nothing-to-review")


@dataclass
class Result:
    outcome: str
    exit_code: int
    message: str
    paid_attempts: int = 0
    log: list = field(default_factory=list)


@dataclass(frozen=True)
class Event:
    pr_number: int
    event_head_sha: str  # empty for workflow_dispatch
    force: bool


class EventError(Exception):
    pass


def parse_event(event_name, payload):
    """Read only the PR number, head SHA and force flag from the event payload."""
    if not isinstance(payload, dict):
        raise EventError("event payload is not an object")
    if event_name == "pull_request_target":
        pr = payload.get("pull_request")
        if not isinstance(pr, dict):
            raise EventError("event has no pull_request")
        number, sha = pr.get("number"), (pr.get("head") or {}).get("sha")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise EventError("invalid pull request number")
        if not (isinstance(sha, str) and SHA_RE.fullmatch(sha)):
            raise EventError("invalid head SHA in event")
        return Event(number, sha, False)
    if event_name == "workflow_dispatch":
        inputs = payload.get("inputs") or {}
        raw = str(inputs.get("pr_number", "")).strip()
        if not re.fullmatch(r"[1-9]\d{0,8}", raw):
            raise EventError("pr_number input must be a positive whole number")
        force = str(inputs.get("force", "false")).strip().lower() == "true"
        return Event(int(raw), "", force)
    raise EventError(f"unsupported event '{event_name}'")


class Runner:
    def __init__(self, cfg, github, openai_factory, event):
        self.cfg = cfg
        self.gh = github
        self.openai_factory = openai_factory  # only called once every gate has passed
        self.event = event
        self.log = []
        self.comment_id = None

    # -- helpers -----------------------------------------------------------
    def _say(self, text):
        self.log.append(text)

    def _status(self, sha, state, description):
        try:
            self.gh.create_status(sha, state, description, STATUS_CONTEXT, self.cfg.run_url)
        except (HTTPError, ValueError) as exc:
            self._say(f"warning: could not set commit status: {exc}")

    def _write_comment(self, body):
        """Update this PR's single review comment, creating it the first time."""
        if self.comment_id is None:
            created = self.gh.create_comment(self.event.pr_number, body)
            cid = (created or {}).get("id")
            if isinstance(cid, int) and not isinstance(cid, bool):
                self.comment_id = cid
        else:
            self.gh.update_comment(self.comment_id, body)

    def _done(self, outcome, code, message, attempts=0):
        self._say(message)
        return Result(outcome, code, message, attempts, self.log)

    # -- main flow ---------------------------------------------------------
    def run(self):
        cfg = self.cfg
        if cfg.mode == "disabled":
            return self._done("disabled", 0, "AI review is disabled (AI_REVIEW_MODE unset or 'disabled'). Nothing done.")
        problem = config_mod.trusted_ref_problem(cfg)
        if problem:
            return self._done("refused", 1, problem)

        pr = self.gh.get_pull(self.event.pr_number)
        if not isinstance(pr, dict):
            return self._done("refused", 1, "could not read the pull request")
        head = pr.get("head") or {}
        base = pr.get("base") or {}
        head_sha, base_sha = head.get("sha", ""), base.get("sha", "")
        if not (SHA_RE.fullmatch(str(head_sha)) and SHA_RE.fullmatch(str(base_sha))):
            return self._done("refused", 1, "pull request has malformed commit SHAs")
        head_repo = (head.get("repo") or {}).get("full_name")
        if head_repo != cfg.repo:
            return self._done("skipped-fork", 0, "pull request comes from a fork; forks are never reviewed")
        if pr.get("state") != "open":
            return self._done("skipped-closed", 0, "pull request is not open")
        if pr.get("draft") is True and not self.event.force:
            return self._done("skipped-draft", 0, "pull request is a draft")
        if self.event.event_head_sha and self.event.event_head_sha != head_sha:
            return self._done(
                "superseded", 0, "a newer commit was pushed after this event; the newer run will review it"
            )

        existing = find_bot_comment(self.gh.list_issue_comments(self.event.pr_number))
        prior_calls = existing.marker.paid_calls if existing else 0
        self.comment_id = existing.comment_id if existing else None
        cap = cfg.max_paid_calls_per_pr

        if (
            cfg.mode == "live"
            and existing
            and existing.marker.sha == head_sha
            and existing.marker.state in NO_REPEAT_STATES
            and not self.event.force
        ):
            return self._done("skipped-duplicate", 0, f"commit {head_sha[:12]} was already reviewed; no new call made")

        try:
            diff_text = self.gh.get_compare_diff(base_sha, head_sha)
        except (HTTPError, ValueError) as exc:
            self._status(head_sha, "error", "AI review failed: could not fetch the diff. Not an approval.")
            return self._done("failed", 1, f"could not fetch diff: {exc}")
        bundle = prepare_diff(diff_text, cfg.max_diff_chars)

        def post(state, detail="", calls=prior_calls, **kwargs):
            marker = Marker(sha=head_sha, state=state, paid_calls=calls)
            body = render_comment(
                marker, model=cfg.model, detail=detail, bundle=bundle, cap=cap, run_url=cfg.run_url, **kwargs
            )
            self._write_comment(body)

        # -- dry run: exercise everything except the paid call --------------
        if cfg.mode == "dry-run":
            payload = build_payload(cfg, bundle, boundary="0" * 32)
            if cfg.price_input_per_mtok > 0 and cfg.price_output_per_mtok > 0:
                est = f"estimated maximum cost if run live: ${estimate_max_cost_usd(cfg, payload, 2):.4f}"
            else:
                est = "cost estimate unavailable (prices not configured)"
            blockers = config_mod.live_blockers(cfg)
            detail = f"Dry run only; OpenAI was not called. {est}. Live mode would currently be blocked by: " + (
                "; ".join(blockers) if blockers else "nothing"
            )
            post("dry-run", detail)
            self._status(head_sha, "failure", "Dry run only - no AI review performed. Not an approval.")
            return self._done("dry-run", 0, detail)

        # -- live: every gate must pass before any paid call ----------------
        blockers = config_mod.live_blockers(cfg)
        if blockers:
            detail = "Live review is not permitted: " + "; ".join(blockers)
            post("blocked", detail)
            self._status(head_sha, "error", "AI review not run: configuration incomplete. Not an approval.")
            return self._done("blocked-config", 1, detail)

        if not bundle.reviewed_files:
            state = "partial" if bundle.is_partial else "nothing-to-review"
            post(state, "No reviewable file changes were sent to the AI.")
            if bundle.is_partial:
                self._status(head_sha, "failure", "AI review incomplete: changes withheld or too large. Not an approval.")
                return self._done("partial", 1, "no reviewable changes; some files were withheld")
            self._status(head_sha, "success", "No reviewable changes for AI review. Advisory; not an approval.")
            return self._done("nothing-to-review", 0, "no reviewable changes")

        if prior_calls >= cap:
            detail = f"Spending limit reached: {prior_calls} paid calls already made on this PR (limit {cap})."
            post("blocked", detail)
            self._status(head_sha, "failure", "AI review not run: per-PR spending limit reached. Not an approval.")
            return self._done("blocked-budget", 1, detail)

        client = self.openai_factory()
        payload = build_payload(cfg, bundle)
        max_cost = estimate_max_cost_usd(cfg, payload, client.max_attempts)
        if max_cost > cfg.max_cost_per_run_usd:
            detail = (
                f"Estimated maximum cost ${max_cost:.4f} exceeds the per-run limit "
                f"${cfg.max_cost_per_run_usd:.4f}. Reduce AI_REVIEW_MAX_DIFF_CHARS or raise the limit."
            )
            post("blocked", detail)
            self._status(head_sha, "failure", "AI review not run: per-run cost limit. Not an approval.")
            return self._done("blocked-budget", 1, detail)

        # Reserve the call in the PR's counter BEFORE spending, so a cancelled or
        # crashed run still counts against the limit.
        reserved = prior_calls + 1
        post("in-progress", "Review in progress.", calls=reserved)
        self._status(head_sha, "pending", f"AI review in progress for {head_sha[:7]}. Not an approval.")

        state, detail, review, notes, usage_line = "failed", "", None, [], ""
        try:
            response = client.create_response(payload)
            text = extract_output_text(response)
            review, notes = validate_review(text, set(bundle.reviewed_files))
            usage = response.get("usage")
            cost = usage_cost_usd(cfg, usage)
            if cost is not None:
                usage_line = (
                    f"{usage['input_tokens']} input / {usage['output_tokens']} output tokens, "
                    f"about ${cost:.4f} at the configured prices"
                )
            state = "partial" if bundle.is_partial else "completed"
        except OpenAIError as exc:
            detail = f"OpenAI request failed: {exc}"
        except ReviewValidationError as exc:
            detail = f"The AI response was rejected by validation: {exc}"
        calls = prior_calls + max(client.attempts, 1)

        # Verify the PR still points at the reviewed commit before reporting.
        current = self.gh.get_pull(self.event.pr_number)
        current_head = ((current or {}).get("head") or {}).get("sha", "")
        stale = current_head != head_sha
        may_write = True
        if stale:
            latest = find_bot_comment(self.gh.list_issue_comments(self.event.pr_number))
            if latest is not None and latest.marker.sha != head_sha:
                may_write = False  # a newer run owns the comment; never overwrite it
                self._say("newer review comment exists; leaving it untouched")
        if may_write:
            post(
                state, detail, calls=calls, review=review, notes=notes, usage_line=usage_line,
                stale_head=current_head if stale else "",
            )

        # Statuses are attached to the reviewed SHA only, so they can never
        # describe a newer commit.
        if state == "completed":
            n = len(review["findings"])
            self._status(head_sha, "success", f"Advisory AI review completed ({n} findings). Not an approval.")
            code = 0
        elif state == "partial":
            self._status(head_sha, "failure", "AI review PARTIAL: some changes not reviewed. Not an approval.")
            code = 1
        else:
            self._status(head_sha, "error", "AI review FAILED - no review performed. Not an approval.")
            code = 1
        msg = f"{state}: {detail}" if detail else state
        if stale:
            msg += f" (stale: PR moved to {current_head[:12]})"
        return self._done(state, code, msg, client.attempts)


def load_event(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
