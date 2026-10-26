# Dexter Progress Report

**Date:** 2026-10-26
**Repository:** ronnieshorthose-cpu/dexter-sandbox
**Branch:** dexter/phase-2b-ai-reviewer
**Pull request:** PR_LINK_PLACEHOLDER

## Assignment

Implement the approved Phase 2B design: a GitHub Actions workflow, review script, and automated tests for an independent, advisory AI reviewer. Keep real OpenAI calls off by default, verify model names and prices against official sources, test the environment-secret assumptions, and open a PR without merging it or activating any paid service.

## Current status

Awaiting human review. The reviewer is built and tested but **switched off**: no secrets, no API key, no billing, and no repository settings were created or changed. PR #3 was not touched.

## Completed tasks

- Reviewer workflow `.github/workflows/ai-review.yml`: off unless `AI_REVIEW_MODE` is set. It runs from `main` only, never checks out PR code, and has a `dry-run` job (no secret) and a `live` job (in the `ai-review` environment).
- Reviewer code `.github/ai_review/dexter_review/`: standard library only.
- Automated tests `.github/ai_review/reviewer_tests/` (99 tests), plus an unprivileged test workflow `.github/workflows/ai-review-tests.yml`.
- Environment probe `.github/workflows/ai-review-env-probe.yml`: proves the secret branch restriction with a dummy secret before a real key exists.
- Operator guide with exact activation steps: `.github/ai_review/README.md`.
- A local dry run against PR #3's real diff, using an in-memory fake GitHub (nothing was posted): no OpenAI client was created, and the estimates and comment came out as expected.

## Verification of models, prices and GitHub behaviour

- **The official websites were blocked.** The environment's network policy blocked both `developers.openai.com` and `docs.github.com` (proxy returned 403). I used official sources hosted on GitHub instead.
- **Model IDs: verified.** `gpt-6.1-sol`, `gpt-6-sol`, `gpt-6-astra`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` and `gpt-5.5` all appear in OpenAI's official Python SDK model list (`openai/openai-python@5d72315`, `src/openai/types/shared/chat_model.py`). Only these are accepted; any other name blocks live mode.
- **Prices: NOT verified.** Prices only appear on OpenAI's website, which I could not reach. A web search snippet listed `gpt-6.1-sol` at $2 input / $10 output per 1M tokens, but I did not treat that as verified. No prices are built into the code, and live mode stays blocked until the owner enters prices checked on the official pricing page.
- **Responses API fields: checked.** `text.format` json_schema, `strict`, `store`, `max_output_tokens`, `status`/`incomplete_details` and `usage` were checked against OpenAI's official OpenAPI spec (`openai/openai-openapi`, `master`).
- **GitHub behaviour: checked.** I read GitHub's docs source (`github/docs@02c25d1`) for `pull_request_target`, environment branch rules, the automatic creation of missing environments, and the new `pull_request_target` default policy. Summary in the README's "Security assumptions".
- **actions/checkout: pinned.** Pinned to v7.0.1, commit `3d3c42e5aac5ba805825da76410c181273ba90b1` (released 2026-07-17). v7 refuses fork checkouts under `pull_request_target`.

## Test results

- **Tests run:** `python3 -m unittest discover -s .github/ai_review/reviewer_tests -t .github/ai_review -v` (Python 3.13.17)
- **Result:** Passed: 99 tests, 0 failures, 0 errors.
- **Coverage of the required areas:**
  - Duplicate reviews (7): same commit skipped, force re-review counted, failed reviews retryable, new commit reviewed, forged human "already reviewed" markers ignored.
  - Failures (9): server errors after retry, quota errors not retried, incomplete responses, refusals, invalid JSON, schema violations, an "approve" field rejected, diff-fetch failure, status-write failure.
  - Partial diffs (5): size limit, credential withheld, only-secret files, lockfile exclusions.
  - Spending limits (5, one of which checks 7 separate blockers): per-PR cap, per-run cost ceiling, each live gate on its own, the reservation recorded before the call, retries counted.
  - API response validation (6): schema, types, lengths, unknown files, output extraction.
  - OpenAI and GitHub HTTP clients (10): retry rules, key only in the auth header, no token or body in errors, SHA validation, pagination.
  - Concurrent updates and SHA checks (5): superseded event, push during review marked STALE, a newer run's comment never overwritten, comment-listing lag, newest bot comment used.
  - Injection and output safety (12):
    - 3 event-parsing tests, including shell-injection attempts in the PR number;
    - 2 output-safety tests (forged markers, banners and links in model output; key never in output);
    - 4 prompt and schema tests (the diff cannot close its untrusted block, no approval field);
    - 3 sanitiser and marker tests.
  - Safe defaults, trust boundaries and entry point (13): disabled by default, dry run never calls OpenAI, non-main refs and forks refused, no network on disabled or misconfigured runs, no tracebacks.
  - Config (7), diff handling (3), happy path (3).
  - Workflow static checks (14): no `${{ }}` inside shell, actions pinned to a full SHA, no PR checkout, read-only top-level permissions, only the live job gets the secret.
- **Mutation check:** I deliberately broke 8 safety checks one at a time (duplicate skip, HTML escaping, cap off-by-one, post-review SHA check, zero default cap, bot-only markers, secret withholding, quota retry). The tests failed every time, and the code was restored afterwards.
- **Not run:** nothing on GitHub Actions or OpenAI. The test workflow will run on this PR; it is read-only and free, because the repository is public.
- **Independent AI review:** Not performed. The capability is built but not activated. The review below is a self-review by the same agent that wrote the code.

## Self-review findings (not independent)

- Fixed during self-review: the reservation step re-listed comments to find the one it had just created, so a delay in GitHub's listing could have produced a duplicate comment. It now uses the ID returned on creation, and a test covers this.
- Fixed during self-review: unexpected errors could print a Python traceback that might echo data. They now print only the error type, with a test.
- Fixed during self-review: a dry-run comment said "Reviewed commit". It now says "Commit".
- Remaining risks are listed below.

## Problems encountered

- The official documentation websites were blocked by the session's network policy. I worked around this with official GitHub-hosted sources, but **prices remain unverified**.
- A test-pattern bug: `statuses: write` matched the `uses:` check. Fixed by anchoring the pattern.
- A stale bytecode cache made one mutation look as if it survived. A re-run without the cache confirmed the tests catch it.
- **Branch naming:** I used `dexter/phase-2b-ai-reviewer`, following your Dexter naming convention, instead of the session's default branch name.

## Estimated additional costs

- None incurred. No API key, billing, or paid service was created or used.
- **If activated later**, the figures below use the unverified $2 / $10 per 1M tokens prices and must be rechecked:
  - PR #3 is about 27 KB of diff, which is about 10,400 input tokens.
  - The worst case is about $0.06 per attempt and $0.12 per run (2 attempts at the full 4,000-token output allowance).
  - A typical run is likely $0.03–0.06.
  - Any cost still needs your approval through the activation steps.

## Unresolved risks

1. **Prices are unverified.** You must confirm the model and prices on OpenAI's pricing page.
2. **`main` has no branch protection.** Anyone with write access, Dexter included, could push to `main` directly and change the reviewer, and the reviewer runs with the key. Branch protection on `main` (requiring a PR) would close this. It is a settings change, so it is your decision.
3. **The new GitHub default policy** may block `pull_request_target` in this public repository. Manual reviews would still work. The probe shows which case applies.
4. **The environment must be created and restricted before** `AI_REVIEW_MODE=live` or `AI_REVIEW_PROBE=enabled` is set. Otherwise GitHub creates it with no protection.
5. **The per-PR spending counter lives in the bot's comment.** Anyone with write access can edit it, and two overlapping runs could undercount by one. Overall spending is capped only by the OpenAI project budget.
6. **Dry-run comments overwrite the PR's review comment.** Switching an already-reviewed PR to dry-run replaces its review text, though the spending count is kept.
7. **The reviewer sees only the diff,** so it can miss problems that depend on code outside it.
8. **Required approvals:** you can't approve PRs opened under your own account, so "require approving review" can't be enabled until Dexter has its own GitHub identity.

## Pending human decisions

- **Review and decide on merging this PR.** Recommendation: merge once you are satisfied. It stays inert until configured.
- **Whether to enable branch protection on `main`.** Recommended before going live.
- **Whether to allow `pull_request_target`** if GitHub's default policy blocks it, or to use manual reviews only.
- **Model and prices** after checking OpenAI's pricing page.

## Next recommended action

You review this PR. If approved and merged, follow `.github/ai_review/README.md` → Activation, steps 2–5. Those steps are free: settings check, environment, probe, then dry run on PR #3. Stop there for a joint review before any paid step (6–8).
