"""Entry point: python3 -m dexter_review (run from .github/ai_review)."""

import os
import sys
import time

from .clients import GitHubClient, HTTPError, OpenAIClient
from .config import ConfigError, load_config
from .runner import EventError, Runner, load_event, parse_event


def write_summary(text):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass


def main():
    if os.environ.get("AI_REVIEW_MODE", "").strip() in ("", "disabled"):
        print("AI review is disabled (AI_REVIEW_MODE unset or 'disabled'). Nothing done.")
        return 0
    try:
        cfg = load_config(os.environ)
    except ConfigError as exc:
        # Malformed configuration never results in a paid call.
        print(f"AI review configuration error: {exc}. No API call was made.")
        write_summary(f"### Dexter AI review\nConfiguration error: {exc}. No API call was made.")
        return 1
    try:
        event = parse_event(cfg.event_name, load_event(os.environ.get("GITHUB_EVENT_PATH", "")))
    except (EventError, OSError, ValueError) as exc:
        print(f"Refusing to run: {exc}")
        return 1

    github = GitHubClient(cfg.repo, cfg.github_token)

    def openai_factory():
        return OpenAIClient(cfg.openai_api_key, sleep=time.sleep)

    try:
        result = Runner(cfg, github, openai_factory, event).run()
    except HTTPError as exc:
        print(f"AI review stopped: {exc}")
        write_summary(f"### Dexter AI review\nStopped: {exc}")
        return 1
    except Exception as exc:  # never dump tracebacks that could echo request data
        print(f"AI review stopped by an unexpected {type(exc).__name__}.")
        write_summary(f"### Dexter AI review\nStopped by an unexpected {type(exc).__name__}.")
        return 1
    for line in result.log:
        print(line)
    write_summary(
        f"### Dexter AI review\n- Outcome: **{result.outcome}**\n- Paid API attempts this run: "
        f"{result.paid_attempts}\n- {result.message}\n- Advisory only; not an approval."
    )
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
