"""Configuration and the safety gates that keep paid API calls off by default."""

import math
import re
from dataclasses import dataclass, field

MODES = ("disabled", "dry-run", "live")

# Model IDs confirmed to exist in OpenAI's official Python SDK model list
# (openai/openai-python@5d72315876ffd8b72b958623ef4db26da3fb1e61,
# src/openai/types/shared/chat_model.py, fetched 2026-10-26). The official
# pricing page could not be reached from the build environment, so prices are
# NOT hard-coded: the owner must enter them after checking
# https://developers.openai.com/api/docs/pricing.
VERIFIED_MODEL_IDS = frozenset(
    {
        "gpt-6-astra",
        "gpt-6.1-sol",
        "gpt-6-sol",
        "gpt-6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
    }
)

REASONING_EFFORTS = ("low", "medium", "high")

# Hard upper bounds that configuration cannot exceed.
MAX_OUTPUT_TOKENS_LIMIT = 16000
MAX_DIFF_CHARS_LIMIT = 400_000
MAX_PAID_CALLS_PER_PR_LIMIT = 50
MAX_COST_PER_RUN_LIMIT_USD = 5.0

# Value the owner must set to confirm they approve paid calls.
PAID_CALLS_APPROVAL_PHRASE = "approved-by-owner"

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ConfigError(Exception):
    """A configuration value is malformed. Treated as 'do not call the API'."""


@dataclass(frozen=True)
class Config:
    mode: str
    repo: str
    event_name: str
    ref: str
    default_branch: str
    server_url: str
    run_id: str
    model: str
    price_input_per_mtok: float
    price_output_per_mtok: float
    max_paid_calls_per_pr: int
    max_cost_per_run_usd: float
    max_output_tokens: int
    max_diff_chars: int
    reasoning_effort: str
    paid_calls_approved: bool
    github_token: str = field(repr=False, default="")
    openai_api_key: str = field(repr=False, default="")

    @property
    def run_url(self):
        if not self.run_id:
            return ""
        return f"{self.server_url}/{self.repo}/actions/runs/{self.run_id}"


def _int(env, name, default, lo, hi):
    raw = env.get(name, "").strip()
    if raw == "":
        return default
    if not re.fullmatch(r"\d+", raw):
        raise ConfigError(f"{name} must be a whole number")
    value = int(raw)
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")
    return value


def _money(env, name, hi):
    raw = env.get(name, "").strip()
    if raw == "":
        return 0.0
    if not re.fullmatch(r"\d+(\.\d+)?", raw):
        raise ConfigError(f"{name} must be a non-negative decimal number")
    value = float(raw)
    if not math.isfinite(value) or value > hi:
        raise ConfigError(f"{name} must be at most {hi}")
    return value


def load_config(env):
    """Build a Config from environment variables. Unset values mean 'off'."""
    mode = env.get("AI_REVIEW_MODE", "").strip() or "disabled"
    if mode not in MODES:
        raise ConfigError(f"AI_REVIEW_MODE must be one of {', '.join(MODES)}")
    repo = env.get("GITHUB_REPOSITORY", "")
    if not _REPO_RE.fullmatch(repo):
        raise ConfigError("GITHUB_REPOSITORY is missing or malformed")
    effort = env.get("AI_REVIEW_REASONING_EFFORT", "").strip()
    if effort and effort not in REASONING_EFFORTS:
        raise ConfigError("AI_REVIEW_REASONING_EFFORT must be low, medium, or high")
    return Config(
        mode=mode,
        repo=repo,
        event_name=env.get("GITHUB_EVENT_NAME", ""),
        ref=env.get("GITHUB_REF", ""),
        default_branch=env.get("AI_REVIEW_DEFAULT_BRANCH", "").strip() or "main",
        server_url=env.get("GITHUB_SERVER_URL", "https://github.com"),
        run_id=env.get("GITHUB_RUN_ID", "") if env.get("GITHUB_RUN_ID", "").isdigit() else "",
        model=env.get("AI_REVIEW_MODEL", "").strip(),
        price_input_per_mtok=_money(env, "AI_REVIEW_PRICE_INPUT_USD_PER_MTOK", 1000.0),
        price_output_per_mtok=_money(env, "AI_REVIEW_PRICE_OUTPUT_USD_PER_MTOK", 1000.0),
        max_paid_calls_per_pr=_int(
            env, "AI_REVIEW_MAX_PAID_CALLS_PER_PR", 0, 0, MAX_PAID_CALLS_PER_PR_LIMIT
        ),
        max_cost_per_run_usd=_money(
            env, "AI_REVIEW_MAX_COST_PER_RUN_USD", MAX_COST_PER_RUN_LIMIT_USD
        ),
        max_output_tokens=_int(
            env, "AI_REVIEW_MAX_OUTPUT_TOKENS", 4000, 256, MAX_OUTPUT_TOKENS_LIMIT
        ),
        max_diff_chars=_int(
            env, "AI_REVIEW_MAX_DIFF_CHARS", 100_000, 1000, MAX_DIFF_CHARS_LIMIT
        ),
        reasoning_effort=effort,
        paid_calls_approved=(
            env.get("AI_REVIEW_PAID_CALLS_APPROVED", "").strip() == PAID_CALLS_APPROVAL_PHRASE
        ),
        github_token=env.get("GH_TOKEN", ""),
        openai_api_key=env.get("OPENAI_API_KEY", ""),
    )


def live_blockers(cfg):
    """Return every reason a paid API call is not permitted. Empty means allowed."""
    blockers = []
    if cfg.mode != "live":
        blockers.append(f"AI_REVIEW_MODE is '{cfg.mode}', not 'live'")
    if not cfg.paid_calls_approved:
        blockers.append(
            f"AI_REVIEW_PAID_CALLS_APPROVED is not set to '{PAID_CALLS_APPROVAL_PHRASE}'"
        )
    if cfg.model not in VERIFIED_MODEL_IDS:
        blockers.append("AI_REVIEW_MODEL is not in the verified model list")
    if cfg.price_input_per_mtok <= 0 or cfg.price_output_per_mtok <= 0:
        blockers.append("model prices are not configured")
    if cfg.max_paid_calls_per_pr <= 0:
        blockers.append("AI_REVIEW_MAX_PAID_CALLS_PER_PR is 0")
    if cfg.max_cost_per_run_usd <= 0:
        blockers.append("AI_REVIEW_MAX_COST_PER_RUN_USD is 0")
    if not cfg.openai_api_key:
        blockers.append("OPENAI_API_KEY is not available to this job")
    return blockers


def trusted_ref_problem(cfg):
    """The reviewer must only ever run from the default branch's workflow."""
    expected = f"refs/heads/{cfg.default_branch}"
    if cfg.ref != expected:
        return f"refusing to run: GITHUB_REF is '{cfg.ref}', expected '{expected}'"
    if cfg.event_name not in ("pull_request_target", "workflow_dispatch"):
        return f"refusing to run on event '{cfg.event_name}'"
    return None
