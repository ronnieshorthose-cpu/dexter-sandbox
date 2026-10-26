# Dexter advisory AI reviewer

An independent, **advisory** AI code reviewer for pull requests. It reads the PR
diff as data, optionally sends it to an OpenAI model, and posts one clearly
labelled comment plus a commit status pinned to the reviewed commit.

It **never** approves, merges, deploys, checks out or runs PR code, or edits
application code. A human must still review and approve every PR.

**Current state: OFF.** With no configuration, the workflow jobs are skipped
and the script exits without contacting GitHub or OpenAI. Zero paid calls are
permitted until the owner completes every step in [Activation](#activation).

## How it works

```
PR opened/updated (pull_request_target) or manual run (workflow_dispatch)
  │  workflow + reviewer code always come from main (trusted)
  ▼
Job is skipped unless AI_REVIEW_MODE is "dry-run" or "live", the ref is main,
and the PR branch is in this repository (forks are never reviewed)
  ▼
Script: re-reads the PR from the API → refuses forks/closed/drafts → stops if a
newer commit exists → stops if this commit was already reviewed → fetches the
diff pinned to the exact base/head SHAs → withholds lockfiles, binaries,
.env/key files and anything that looks like a credential → caps the size
  ▼
dry-run: posts "DRY RUN — no AI review performed" (no OpenAI call, no key)
live:    checks every spending gate → reserves the call in the PR's counter →
         calls OpenAI (Responses API, strict JSON schema, store=false, no tools)
         → validates the reply → re-checks the PR head SHA → posts the result
  ▼
Commit status "Dexter AI review (advisory)" on the reviewed SHA only:
  success  = review ran to completion (NOT approval)
  failure  = partial review, dry run, or a spending limit stopped it
  error    = review failed or is not configured
```

## Files

| Path | Purpose |
|---|---|
| `.github/workflows/ai-review.yml` | The reviewer workflow (`dry-run` job without secrets, `live` job in the `ai-review` environment) |
| `.github/workflows/ai-review-tests.yml` | Runs the tests on every PR (read-only, no secrets) |
| `.github/workflows/ai-review-env-probe.yml` | Proves the environment's branch restriction with a dummy secret, before a real key exists |
| `.github/ai_review/dexter_review/` | Reviewer code (Python standard library only) |
| `.github/ai_review/reviewer_tests/` | Automated tests (no network) |

Run the tests: `python3 -m unittest discover -s .github/ai_review/reviewer_tests -t .github/ai_review -v`

## Configuration (all unset by default)

Repository variables (Settings → Secrets and variables → Actions → Variables):

| Variable | Default | Meaning |
|---|---|---|
| `AI_REVIEW_MODE` | unset = disabled | `disabled`, `dry-run`, or `live`. **This is the kill switch.** |
| `AI_REVIEW_PAID_CALLS_APPROVED` | unset | Must be exactly `approved-by-owner` for live calls |
| `AI_REVIEW_MODEL` | unset | Must be in the verified list in `config.py` |
| `AI_REVIEW_PRICE_INPUT_USD_PER_MTOK` | unset | Input price per 1M tokens, copied by the owner from OpenAI's pricing page |
| `AI_REVIEW_PRICE_OUTPUT_USD_PER_MTOK` | unset | Output price per 1M tokens (reasoning tokens bill as output) |
| `AI_REVIEW_MAX_PAID_CALLS_PER_PR` | `0` | Paid API attempts allowed per PR (hard ceiling 50) |
| `AI_REVIEW_MAX_COST_PER_RUN_USD` | `0` | Worst-case cost allowed per run (hard ceiling $5) |
| `AI_REVIEW_MAX_OUTPUT_TOKENS` | `4000` | Output cap per call (256–16000) |
| `AI_REVIEW_MAX_DIFF_CHARS` | `100000` | Diff size sent (1000–400000); larger diffs are reported PARTIAL |
| `AI_REVIEW_REASONING_EFFORT` | unset | Optional `low`/`medium`/`high` |
| `AI_REVIEW_PROBE` | unset | `enabled` turns on the environment probe workflow |

Environment secret (Settings → Environments → `ai-review` → Secrets): `OPENAI_API_KEY`.

Before any live call the script requires **all** of: mode `live`, the approval
phrase, a verified model, both prices, a non-zero per-PR cap, a non-zero
per-run cost limit, the key, the default-branch ref, and a same-repository PR.
It also refuses if its worst-case estimate (full input + maximum output, times
2 attempts) is above the per-run limit.

## Security assumptions (verified against GitHub's documentation, 2026-10-26)

Source: `github/docs` repository at commit `02c25d1` (the docs website is
blocked from the build environment, so the source files were read directly).

1. **`pull_request_target` runs the default branch's workflow.** On github.com,
   `GITHUB_SHA` is the last commit on the default branch and `GITHUB_REF` is the
   default branch, and `actions/checkout` without a `ref` checks out the default
   branch. So PR authors cannot change the reviewer code or workflow.
2. **Environment branch rules are matched against `GITHUB_REF`.** With the
   `ai-review` environment restricted to `main`:
   - `pull_request_target` → `GITHUB_REF` is `main` → allowed;
   - `workflow_dispatch` → `GITHUB_REF` is the branch picked in the UI → allowed only for `main`;
   - `pull_request` → `GITHUB_REF` is `refs/pull/N/merge` → **denied**, so a PR
     that adds its own workflow cannot read the key.
   The branch rule does **not** distinguish fork PRs for `pull_request_target`
   (the ref is still `main`). Forks are kept out by the job `if`, the script's own
   same-repository check, and `actions/checkout` v7 refusing fork checkouts.
3. **A missing environment is created automatically with no protection** when a
   job references it. That is why the `live` job and the probe are gated on
   variables that must only be set after the environment is configured.
4. **Public repository on the Free plan:** environment secrets, deployment
   branch rules, and required reviewers are available (they are not for private
   repositories on Free).
5. **New default policy:** GitHub is adding a default policy that blocks
   `pull_request_target` in public repositories (in "evaluate" mode now,
   enforced from 2026-12-02 for affected repositories). If it blocks this repo,
   automatic reviews will not start; manual `workflow_dispatch` reviews still
   work. Allowing `pull_request_target` needs an Actions event policy, which is
   an owner decision.
6. **Comments made with `GITHUB_TOKEN` do not trigger workflows**, and this
   workflow has no comment triggers, so it cannot loop.

These assumptions are tested by the environment probe (step 3 below) before any
real key is stored.

## Activation

Do these in order. Stop at any unexpected result. Nothing here is done by Dexter.

1. **Review and merge this PR** (owner decision). Nothing runs after merging.
2. **Repository settings** (Settings → Actions → General):
   - confirm "Allow GitHub Actions to create and approve pull requests" is **off**;
   - check whether the `pull_request_target` default policy applies to this repository.
3. **Create the environment first** (Settings → Environments → New environment → `ai-review`):
   - Deployment branches and tags → **Selected branches and tags** → add `main` only;
   - Required reviewers → add yourself (so every live run waits for your approval);
   - consider turning off "Allow administrators to bypass configured protection rules".
4. **Probe with a dummy secret** (no real key yet):
   - add environment secret `AI_REVIEW_CANARY` = `canary-not-a-real-key`;
   - set repository variable `AI_REVIEW_PROBE` = `enabled`;
   - Actions → "AI review environment probe" → Run workflow on **main** → expect `canary=present`;
   - Run workflow on **another branch** → expect the job to be rejected by environment rules;
   - open a throwaway same-repo PR → expect the `pull_request` run to be **rejected**,
     and the `pull_request_target` run to show `canary=present` (if it does not
     start at all, the default policy in step 2 is blocking it);
   - then set `AI_REVIEW_PROBE` = `disabled` and delete `AI_REVIEW_CANARY`.
5. **Dry run (free)**: set `AI_REVIEW_MODE` = `dry-run`. Actions → "Dexter AI review (advisory)"
   → Run workflow → `pr_number` = `3`. Expect one comment on PR #3 saying
   "DRY RUN — no AI review was performed" and a red status "Dry run only". No OpenAI call.
6. **OpenAI account (owner)**: create a separate project, set a monthly budget
   with alerts, and create a key restricted to that project. On
   https://developers.openai.com/api/docs/pricing confirm the model ID and its
   input and output prices.
7. **Configure live**: add environment secret `OPENAI_API_KEY`; set variables
   `AI_REVIEW_MODEL`, both prices, `AI_REVIEW_MAX_PAID_CALLS_PER_PR` (suggest `3`),
   `AI_REVIEW_MAX_COST_PER_RUN_USD` (suggest `0.50`), and finally
   `AI_REVIEW_PAID_CALLS_APPROVED` = `approved-by-owner` and `AI_REVIEW_MODE` = `live`.
8. **First live review**: Run workflow with `pr_number` = `3`, approve the
   environment prompt, and check the comment, status, and OpenAI usage page.

**To stop everything at once:** set `AI_REVIEW_MODE` to `disabled` (or delete it).

## Known limitations

- Prices are not built in; the owner must keep the two price variables current.
- The per-PR counter lives in the bot's comment. Anyone with write access can edit
  that comment, and two overlapping runs could undercount by one. Organisation-wide
  spending is capped only by the OpenAI project budget.
- The reviewer sees only the diff, not the whole repository.
- Anyone with write access can change `main`, and so the reviewer, until branch
  protection is enabled on `main`.
