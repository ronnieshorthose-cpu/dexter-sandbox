"""Orchestrates one reviewer run. All I/O goes through injected clients."""

import json
import math
import re
from dataclasses import dataclass, field

from . import config as config_mod
from .clients import SHA_RE, HTTPError, OpenAIError
from .diffing import check_file_count, prepare_diff
from .render import BOT_LOGIN, STATUS_CONTEXT, Marker, find_bot_comment, render_comment
from .review import (
    ReviewValidationError,
    build_payload,
    estimate_max_cost_usd,
    extract_output_text,
    max_cost_per_attempt_usd,
    usage_cost_usd,
    validate_review,
)

# Re-running these states on the same commit would only repeat the same paid call.
NO_REPEAT_STATES = ("completed", "partial", "nothing-to-review")
# Automatic runs (pull_request_target) also never retry a failed or interrupted
# review of the same commit; only a person starting a manual run can.
NO_AUTO_REPEAT_STATES = NO_REPEAT_STATES + ("failed", "in-progress")

# Reservation statuses double as a spending ledger. GitHub has no way to delete
# a commit status, so the ledger survives a deleted or edited bot comment.
_RESERVATION_RE = re.compile(r"reserved (\d{1,3}) paid call\(s\), max \$(\d{1,4}\.\d{6})")
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")


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

    @property
    def automatic(self):
        return bool(self.event_head_sha)


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


def _micro(usd):
    return math.ceil(round(usd * 1_000_000, 6))


def reservation_description(sha, calls, cost_micro):
    return (
        f"AI review in progress for {sha[:7]}: reserved {calls} paid call(s), "
        f"max ${cost_micro / 1_000_000:.6f}. Not an approval."
    )


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

    def _ledger(self, sha):
        """Paid calls and worst-case cost (micro-USD) reserved on `sha`, from commit statuses."""
        calls, cost = 0, 0
        for status in self.gh.list_statuses(sha):
            if not isinstance(status, dict):
                continue
            creator = status.get("creator") or {}
            if creator.get("login") != BOT_LOGIN or status.get("context") != STATUS_CONTEXT:
                continue
            if status.get("state") != "pending":
                continue
            match = _RESERVATION_RE.search(str(status.get("description", "")))
            if match:
                calls += int(match.group(1))
                cost += _micro(float(match.group(2)))
        return calls, cost

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
        self.comment_id = existing.comment_id if existing else None
        cap = cfg.max_paid_calls_per_pr
        prior_calls = existing.marker.paid_calls if existing else 0
        if existing and existing.marker.cost_micro is not None:
            prior_cost = existing.marker.cost_micro
        else:
            # Older markers recorded no cost: assume every call used the whole per-run limit.
            prior_cost = prior_calls * _micro(cfg.max_cost_per_run_usd)

        if cfg.mode == "live" and existing and existing.marker.sha == head_sha and not self.event.force:
            repeat = NO_AUTO_REPEAT_STATES if self.event.automatic else NO_REPEAT_STATES
            if existing.marker.state in repeat:
                if existing.marker.state in NO_REPEAT_STATES:
                    why = f"commit {head_sha[:12]} was already reviewed; no new call made"
                else:
                    why = (
                        f"the last review of commit {head_sha[:12]} ended as '{existing.marker.state}'; "
                        "automatic runs never retry it. Start a manual run to retry (it counts toward the limit)."
                    )
                return self._done("skipped-duplicate", 0, why)

        try:
            diff_text = self.gh.get_compare_diff(base_sha, head_sha)
        except (HTTPError, ValueError) as exc:
            self._status(head_sha, "error", "AI review failed: could not fetch the diff. Not an approval.")
            return self._done("failed", 1, f"could not fetch diff: {exc}")
        bundle = prepare_diff(diff_text, cfg.max_diff_chars)
        check_file_count(bundle, pr.get("changed_files"))

        def post(state, detail="", calls=None, cost=None, **kwargs):
            marker = Marker(
                sha=head_sha,
                state=state,
                paid_calls=prior_calls if calls is None else calls,
                cost_micro=prior_cost if cost is None else cost,
            )
            body = render_comment(
                marker, model=cfg.model, detail=detail, bundle=bundle, cap=cap,
                cost_cap=cfg.max_cost_per_pr_usd, base_sha=base_sha, run_url=cfg.run_url, **kwargs
            )
            self._write_comment(body)

        # -- dry run: exercise everything except the paid call --------------
        if cfg.mode == "dry-run":
            payload = build_payload(cfg, bundle, boundary="0" * 32)
            if cfg.price_input_per_mtok > 0 and cfg.price_output_per_mtok > 0:
                worst = estimate_max_cost_usd(cfg, payload, config_mod.MAX_ATTEMPTS_PER_RUN)
                est = f"estimated maximum cost if run live: ${worst:.4f}"
            else:
                est = "cost estimate unavailable (prices not configured)"
            blockers = config_mod.live_blockers(cfg)
            detail = f"Dry run only; OpenAI was not called. {est}. Live mode would currently be blocked by: " + (
                "; ".join(blockers) if blockers else "nothing"
            )
            if existing and existing.marker.state != "dry-run":
                # Never replace a real review (or its status) with a dry-run notice.
                detail += " The existing review comment and status were left unchanged."
                return self._done("dry-run", 0, detail)
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

        # The comment can be deleted or edited by anyone with write access; the
        # status ledger cannot be deleted. Use whichever records more spending.
        try:
            ledger_calls, ledger_cost = self._ledger(head_sha)
        except (HTTPError, ValueError) as exc:
            detail = f"Could not read the spending ledger ({exc}). No paid call was made."
            post("blocked", detail)
            self._status(head_sha, "error", "AI review not run: spending ledger unreadable. Not an approval.")
            return self._done("blocked-ledger", 1, detail)
        if ledger_calls > prior_calls or ledger_cost > prior_cost:
            self._say("the spending ledger records more than the review comment; using the ledger")
        prior_calls, prior_cost = max(prior_calls, ledger_calls), max(prior_cost, ledger_cost)

        if prior_calls >= cap:
            detail = f"Spending limit reached: {prior_calls} paid calls already made on this PR (limit {cap})."
            post("blocked", detail)
            self._status(head_sha, "failure", "AI review not run: per-PR spending limit reached. Not an approval.")
            return self._done("blocked-budget", 1, detail)

        payload = build_payload(cfg, bundle)
        per_attempt = _micro(max_cost_per_attempt_usd(cfg, payload))
        by_run = _micro(cfg.max_cost_per_run_usd) // per_attempt
        by_pr_cost = max(_micro(cfg.max_cost_per_pr_usd) - prior_cost, 0) // per_attempt
        allowed = min(config_mod.MAX_ATTEMPTS_PER_RUN, cap - prior_calls, by_run, by_pr_cost)
        if by_run < 1:
            detail = (
                f"Estimated maximum cost ${per_attempt / 1_000_000:.4f} for one attempt exceeds the per-run "
                f"limit ${cfg.max_cost_per_run_usd:.4f}. Reduce AI_REVIEW_MAX_DIFF_CHARS or raise the limit."
            )
            post("blocked", detail)
            self._status(head_sha, "failure", "AI review not run: per-run cost limit. Not an approval.")
            return self._done("blocked-budget", 1, detail)
        if by_pr_cost < 1:
            detail = (
                f"Spending limit reached: ${prior_cost / 1_000_000:.4f} worst-case spend already recorded on this "
                f"PR; another attempt (up to ${per_attempt / 1_000_000:.4f}) would exceed the per-PR limit "
                f"${cfg.max_cost_per_pr_usd:.2f}."
            )
            post("blocked", detail)
            self._status(head_sha, "failure", "AI review not run: per-PR cost limit reached. Not an approval.")
            return self._done("blocked-budget", 1, detail)

        # Reserve the worst case (every allowed attempt) BEFORE spending, in both
        # the undeletable status ledger and the comment, so a cancelled or
        # crashed run still counts against the limits. No ledger, no call.
        reserved_calls = prior_calls + allowed
        reserved_cost = prior_cost + allowed * per_attempt
        try:
            self.gh.create_status(
                head_sha, "pending", reservation_description(head_sha, allowed, allowed * per_attempt),
                STATUS_CONTEXT, cfg.run_url,
            )
        except (HTTPError, ValueError) as exc:
            detail = f"Could not record the paid-call reservation ({exc}). No paid call was made."
            try:
                post("blocked", detail)
            except HTTPError:
                pass
            return self._done("blocked-ledger", 1, detail)
        post("in-progress", "Review in progress.", calls=reserved_calls, cost=reserved_cost)

        client = self.openai_factory()
        client.max_attempts = allowed
        state, detail, review, notes, usage_line, api_model = "failed", "", None, [], "", ""
        try:
            response = client.create_response(payload)
            text = extract_output_text(response)
            review, notes = validate_review(text, set(bundle.reviewed_files))
            reported = response.get("model")
            if isinstance(reported, str) and _MODEL_RE.fullmatch(reported):
                api_model = reported
            usage = response.get("usage")
            cost = usage_cost_usd(cfg, usage)
            if cost is not None:
                usage_line = (
                    f"{usage['input_tokens']} input / {usage['output_tokens']} output tokens, "
                    f"about ${cost:.4f} at the configured prices"
                )
                if _micro(cost) > per_attempt:
                    usage_line += " (MORE than the pre-call estimate; check the configured prices)"
            state = "partial" if bundle.is_partial else "completed"
        except OpenAIError as exc:
            detail = f"OpenAI request failed: {exc}"
        except ReviewValidationError as exc:
            detail = f"The AI response was rejected by validation: {exc}"
        except Exception as exc:  # never leave a reserved review stuck "in progress"
            detail = f"The review stopped unexpectedly ({type(exc).__name__})."
        attempts = max(client.attempts, 1)
        calls = prior_calls + attempts
        spent = prior_cost + attempts * per_attempt

        # Verify the PR still points at the reviewed commit before reporting.
        stale, current_head, head_note = False, "", ""
        try:
            current = self.gh.get_pull(self.event.pr_number)
            current_head = str(((current or {}).get("head") or {}).get("sha", ""))
            if not SHA_RE.fullmatch(current_head):
                raise ValueError("malformed head SHA")
            stale = current_head != head_sha
        except (HTTPError, ValueError):
            current_head = ""
            head_note = "Could not re-check the pull request after the review; this result covers only the commit shown."
        may_write = True
        if stale:
            try:
                latest = find_bot_comment(self.gh.list_issue_comments(self.event.pr_number))
            except HTTPError:
                latest, may_write = None, False
                self._say("could not re-read comments; leaving the review comment untouched")
            if latest is not None and latest.marker.sha != head_sha:
                may_write = False  # a newer run owns the comment; never overwrite it
                self._say("newer review comment exists; leaving it untouched")
        comment_ok = True
        if may_write:
            try:
                post(
                    state, " ".join(part for part in (detail, head_note) if part), calls=calls, cost=spent,
                    review=review, notes=notes, usage_line=usage_line, api_model=api_model,
                    stale_head=current_head if stale else "",
                )
            except HTTPError as exc:
                comment_ok = False
                self._say(f"warning: could not post the review comment: {exc}")

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
        if not comment_ok:
            code = 1
        msg = f"{state}: {detail}" if detail else state
        if stale:
            msg += f" (stale: PR moved to {current_head[:12]})"
        if head_note:
            msg += " (PR head could not be re-checked)"
        if not comment_ok:
            msg += " (review comment could not be posted)"
        return self._done(state, code, msg, client.attempts)


def load_event(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
