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
diff pinned to the exact base/head SHAs → checks the diff's files are exactly
GitHub's changed-file list → withholds lockfiles, minified files, binaries,
sensitive file names and anything that looks like a credential → caps the size
  ▼
dry-run: posts "DRY RUN — no AI review performed" (no OpenAI call, no key);
         never overwrites a real review
live:    checks every gate → reads the spending ledger on every commit of the
         PR → limits this run's attempts to what the per-PR and per-run limits
         allow → reserves the worst case as a commit status → re-reads the
         ledger to confirm → calls OpenAI (Responses API, strict JSON schema,
         store=false, no tools) → validates the reply → re-checks the PR head
         SHA → posts the result
  ▼
Commit status "Dexter AI review (advisory)" on the reviewed SHA only:
  success  = review ran to completion (NOT approval)
  failure  = partial review, dry run, or a spending limit stopped it
  error    = review failed or is not configured
  pending  = a paid call is reserved / in progress (these are the ledger)
```

## Safety controls

See `docs/PHASE-2C-SECURITY-ASSESSMENT.md` for the reasoning and remaining risks.

**Spending.** These are software limits, **not a billing guarantee**. Only the
OpenAI project budget can actually cap charges.
- Off by default; live needs every gate listed under Configuration.
- Per-run limit on the *worst-case* cost: every attempt is assumed to bill its
  full input (one token per UTF-8 byte, plus overhead) and the full output allowance.
- Per-PR limits on paid attempts and on total worst-case spend. Retries count,
  and a run never retries past either limit.
- The worst case (every allowed attempt) is reserved **before** the call as a
  commit status. That ledger is the only record used for limits: the runner adds
  up the reservations on **every commit** of the PR, so pushing a new commit does
  not reset the count. Statuses cannot be deleted, and a forged one can only
  make the count higher. The bot comment is display only, because any workflow
  can post as `github-actions[bot]`. Reservations are never reduced afterwards,
  so a run that needed only one attempt still counts as the attempts it reserved.
- If the ledger cannot be read completely (unreadable statuses, an incomplete
  commit list, or more than 100 commits), or the reservation cannot be recorded
  and then seen again, no call is made.
- After reserving, the runner re-reads the ledger. If another run reserved at the
  same moment and the total is over a limit, it stands down without calling.
- An automatic run never pays twice for one commit: it stops if that commit has
  a reservation or a finished review, whatever the comment says. A manual run
  (counted) can retry.
- A run in progress is never cancelled by a newer one, so a paid response is not thrown away.

**Untrusted input.** PR titles, bodies and comments are never read. File
names, diff text and the list of omitted files go to the model only inside a
random-token boundary, labelled as untrusted data; the trusted instructions name
the token, so a fake boundary line in the diff does not match. The diff is split
into files on line feeds only, so characters such as U+2028 or form feed cannot
start a fake file. The model has no tools. Its
reply must match a strict schema with no approval field. Every text field is
sanitised before posting: no HTML, links, images, mentions, headings, or
invisible/bidirectional characters.

**Reliability.** Every comment names the exact reviewed commit and diff range.
Results are **completed**, **PARTIAL**, **FAILED**, **blocked**, **dry run** or
**nothing to review**. PARTIAL means at least one changed file was not reviewed
in full for any reason: generated (lockfile, minified, source map), sensitive
name, binary, possible secret, truncated, unparseable, missing from GitHub's
diff, extra or duplicated in it, or not confirmed against GitHub's changed-file
list. Only *completed* gives a green status. A review that finishes after the PR
moves is marked **STALE**. Comments always fit GitHub's size limit.

**Failure handling.** Malformed configuration, events, SHAs, API responses,
token counts, or an unreadable spending ledger all stop the run before any paid
call. A failure between the reservation and the call (for example the
in-progress comment cannot be posted) ends FAILED with no call. Errors after a
paid call still end in a FAILED comment and status. If the final status cannot
be set, the run exits with an error, since the commit would still show the
pending reservation. Error messages never include tokens or response bodies.

**Permissions.** The jobs get `contents: read`, `pull-requests: read`,
`issues: write` (to comment, through the issues API) and `statuses: write`. The
token cannot push, merge, or submit a pull request review or approval.

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
| `AI_REVIEW_MAX_COST_PER_PR_USD` | `0` | Worst-case cost allowed per PR, all runs together (hard ceiling $25) |
| `AI_REVIEW_MAX_OUTPUT_TOKENS` | `4000` | Output cap per call (256–16000) |
| `AI_REVIEW_MAX_DIFF_CHARS` | `100000` | Diff size sent (1000–400000); larger diffs are reported PARTIAL |
| `AI_REVIEW_REASONING_EFFORT` | unset | Optional `low`/`medium`/`high` |
| `AI_REVIEW_PROBE` | unset | `enabled` turns on the environment probe workflow |

Environment secret (Settings → Environments → `ai-review` → Secrets): `OPENAI_API_KEY`.

Before any live call the script requires **all** of: mode `live`, the approval
phrase, a verified model, both prices, a non-zero per-PR call cap, non-zero
per-run and per-PR cost limits, the key, the default-branch ref, a
same-repository PR, and a readable spending ledger.
It refuses if one worst-case attempt (full input + maximum output) is above the
per-run limit, or would take the PR over its per-PR cost limit. It makes a
retry only if the second attempt also fits within every limit.

## Security assumptions (verified against GitHub's documentation, 2026-10-26)

Source: `github/docs` repository at commit `02c25d1` (the docs website is
blocked from the build environment, so the source files were read directly).

1. **`pull_request_target` runs the default branch's workflow.** On github.com,
   `GITHUB_SHA` is the last commit on the default branch and `GITHUB_REF` is the
   default branch, and `actions/checkout` without a `ref` checks out the default
   branch. So a PR's own changes to the reviewer do not run in a review of that
   PR. Two cases need more than this: a PR that targets a branch other than
   `main` runs *that branch's* copy of the workflow (the job `if` and the
   environment's `main`-only branch rule are what stop it), and the reviewer on
   `main` is only as trusted as the people who can merge into `main` (see the
   ruleset below).
2. **Environment branch rules are matched against `GITHUB_REF`.** *If* the
   `ai-review` environment is restricted to `main` (not yet created or verified;
   see Activation step 3):
   - `pull_request_target` → `GITHUB_REF` is `main` → allowed;
   - `workflow_dispatch` → `GITHUB_REF` is the branch picked in the UI → allowed only for `main`;
   - `pull_request` → `GITHUB_REF` is `refs/pull/N/merge` → **denied**, so a PR
     that adds its own workflow cannot read the key. Such a workflow *can*
     still use its own `GITHUB_TOKEN` to post comments as `github-actions[bot]`
     (declaring `permissions:` overrides the repository's read-only default),
     which is why the comment is never trusted for spending.
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
7. **`main` is protected by the "Protect Dexter Main" ruleset** (checked through
   GitHub's public rules API on 2026-10-26: ruleset 25842195, enforcement
   `active`, target the default branch). It blocks deletion and force pushes,
   requires a pull request with review threads resolved, and requires the
   `Tests / unit-tests` check. It requires **0** approving reviews, and its
   bypass list could not be read without signing in.

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
   This also confirms that `issues: write` is enough to comment on a pull request
   (not yet verified on GitHub); if the comment step fails, stop and report it.
6. **OpenAI account (owner)**: create a separate project, set a monthly budget
   with alerts, and create a key restricted to that project. On
   https://developers.openai.com/api/docs/pricing confirm the model ID and its
   input and output prices.
7. **Configure live**: add environment secret `OPENAI_API_KEY`; set variables
   `AI_REVIEW_MODEL`, both prices, `AI_REVIEW_MAX_PAID_CALLS_PER_PR` (suggest `4`:
   each run reserves its retry, so this allows two runs per PR),
   `AI_REVIEW_MAX_COST_PER_RUN_USD` (suggest `0.50`), `AI_REVIEW_MAX_COST_PER_PR_USD`
   (suggest `1.50`), and finally `AI_REVIEW_PAID_CALLS_APPROVED` = `approved-by-owner`
   and `AI_REVIEW_MODE` = `live`. The dry run in step 5 shows the worst-case cost per run
   for PR #3. If it is above the per-run limit, lower `AI_REVIEW_MAX_DIFF_CHARS` or raise the limit.
8. **First live review**: Run workflow with `pr_number` = `3`, approve the
   environment prompt, and check the comment, status, and OpenAI usage page.

**To stop everything at once:** set `AI_REVIEW_MODE` to `disabled` (or delete it).

Do not make "Dexter AI review (advisory)" a required status check. Anyone with
`statuses: write` can post a status with that name, and the review is advisory.

## Known limitations

Full list with recommended actions: `docs/PHASE-2C-SECURITY-ASSESSMENT.md`
(R-1 to R-8, and the independent audit remediation section).

- **The software spending limits are not a billing guarantee.** They rely on the
  owner-entered prices and on GitHub data. Only the OpenAI project budget can cap charges.
- **The per-PR limit is per pull request.** Opening a new PR starts a new count,
  and so does force-pushing a PR so that earlier commits drop out of its commit
  list (their reservations still exist but can no longer be found). There is no
  repository-wide or daily cap; per-run approval on the `ai-review` environment
  and the OpenAI project budget cover that.
- The ledger re-check stops two racing runs from overspending only if GitHub
  shows each run's reservation to the other straight away. The workflow's
  concurrency group is the main protection.
- Anyone who can post statuses (any workflow with `statuses: write`) can add fake
  reservations or statuses with the reviewer's name. That can block reviews but
  not lower the count. More than 1,000 statuses on one commit also blocks reviews.
- Prices are one input price and one output price. If a model charges more for
  very long prompts, enter the higher price or keep `AI_REVIEW_MAX_DIFF_CHARS` low.
- The worst-case estimate deliberately over-states cost (about 3 times for ordinary
  code), so the per-PR spend shown in the comment is an upper bound, not a bill.
- Prompt injection and secret detection are mitigated, not eliminated. A
  successful injection could still produce a "completed, no findings" result.
- The reviewer sees only the diff, not the whole repository.
- `main` requires a pull request, but 0 approvals, so anyone with write access
  can merge their own change to the reviewer. The required `Tests / unit-tests`
  check comes from a workflow file that a pull request can itself change.
