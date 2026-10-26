# Dexter Progress Report: Phase 3A project coordinator

**Date:** 2026-10-26
**Repository:** `ronnieshorthose-cpu/dexter-sandbox`
**Branch:** `dexter/phase-3a-project-coordinator`
**Pull request:** https://github.com/ronnieshorthose-cpu/dexter-sandbox/pull/7

## Assignment

Build the first working version of Dexter's project coordination system. It
should be a local task manager with enforced states and human approval
boundaries, manual structured handoffs between ChatGPT and Claude Code, a
beginner-friendly interface, progress reports, tests and documentation. It must
not connect to Eden Well, merge, deploy, call paid APIs or change GitHub
settings.

## Current status

**Awaiting human review.** The coordinator is implemented, tested and
documented on the branch above. A security self-audit of the first version
(`f4cc8a1`) found nine issues. All nine are fixed, and each one has regression
tests; see [PHASE-3A-SECURITY-ASSESSMENT.md](PHASE-3A-SECURITY-ASSESSMENT.md).
Nothing has been merged or deployed.

## Completed tasks

- **Roles** (`dexter_coordinator/models.py`): Human Owner, Dexter, ChatGPT
  (architect and reviewer) and Claude Code (developer), with their duties. Only
  the two AI roles can be assigned work.
- **Task management** (`coordinator.py`, `store.py`): one local SQLite file.
  It stores projects, tasks with unique IDs, descriptions, priorities,
  dependencies (same project, no loops), assignee, state, acceptance criteria,
  reported test results, review findings, approval and acceptance records, and
  a timestamped, hash-chained activity history.
- **States and transitions** (`models.TRANSITIONS`): the suggested path plus
  BLOCKED, REJECTED and CANCELLED. Each move belongs to exactly one role, and
  no move belongs to an AI role. The checks:
  - work starts only after owner approval, with all dependencies COMPLETED;
  - review needs a recorded `done` report (passing tests for Claude Code);
  - the owner's final decision needs an independent review from the other AI role;
  - task content is locked once submitted, and approval is bound to a hash of it.
- **Human approval boundary** (`auth.py`, `coordinator.py`):
  - **Owner actions:** approve, reject, complete, return, cancel, resume,
    recording an AI reply, and confirming a handoff was sent.
  - **Passphrase:** each owner action needs it (scrypt, at least 12
    characters, a growing lockout after repeated wrong attempts).
  - **Signatures and checks:** each owner entry is signed with the owner's key,
    and every owner action first re-checks all records and signatures.
  - **Unverified actions** are never recorded as the owner's, and AI reply
    senders are shown as claims.
  - Every decision is labelled **SIMULATED** and is not connected to GitHub.
  - Pausing needs no passphrase, because it only stops work.
- **Tamper evidence** (`store.py`, `coordinator.py`): a hash-chained history
  with signed owner entries.
  - Each task's history is replayed and compared with its stored state,
    approval and acceptance.
  - Replies and handoffs are checked against the digests recorded at the time.
  - The database structure is validated on every open.
  - `check --owner` verifies every signature and prints an anchor to keep
    elsewhere.
  - Limits are in [COORDINATOR-SECURITY.md](COORDINATOR-SECURITY.md).
- **Handoffs** (`handoff.py`): `dexter-handoff/v1` messages. Each has the
  project and task IDs, roles, objective, context, references, acceptance
  criteria, safety rules, requested action, reply format, timestamp and
  correlation ID.
  - Delivery is never claimed; "mark sent" is an owner confirmation, not a delivery.
  - Replies must match a strict `dexter-response/v1` schema: correlation ID,
    task, sender and outcome must all match the handoff.
  - Replies to handoffs prepared before the task changed are refused.
  - Replies are scanned for instruction-like text and stored as data. They
    never change state.
  - Earlier replies quoted in later handoffs are fenced with random-token
    UNTRUSTED DATA markers.
- **Command line** (`cli.py`): projects, tasks, assignments, the full workflow,
  handoffs, recording replies, history, status, reports, `check` and `backup`.
  Every command prints the next step. Output has control characters removed.
- **Reports** (`report.py`): plain-English progress reports with the sections
  of `DEXTER-REPORT-TEMPLATE.md`.
- **Documentation:**
  - `docs/COORDINATOR.md`: install, use, approvals, checking, recovery,
    working vs planned, security limitations;
  - `docs/COORDINATOR-SECURITY.md`: trust zones, and which safeguards the code
    enforces versus which need separate OS accounts, external services or
    GitHub identity;
  - `docs/PHASE-3A-SECURITY-ASSESSMENT.md`;
  - a README section;
  - `.gitignore` for local databases.
- **Demonstration**: `examples/coordinator_demo.py`, a fictional "Pocket
  Library" project from request to owner acceptance.

### Example task and handoff (from the demo)

```
TASK-0001  Book catalogue model
  State:        IN_PROGRESS (being worked on by the assigned AI)
  Project:      PRJ-001 Pocket Library
  Priority:     high
  Assigned to:  Claude Code (Developer)
  Approval:     APPROVED (SIMULATED human approval)
  Acceptance criteria:
    1. Books can be added and listed
    2. The list is sorted by title
    3. Unit tests cover add and list
```

```
=== DEXTER HANDOFF (dexter-handoff/v1) ===
Handoff:          HO-0002
Correlation ID:   <random UUID>
Project:          PRJ-001  Pocket Library
Task:             TASK-0001  Book catalogue model (priority high, state IN_PROGRESS)
From:             Dexter (Coordinator)
To:               Claude Code (Developer)
Requested action: implement: Carry out the approved task and report the result.
OBJECTIVE / CONTEXT / FILES AND REFERENCES / ACCEPTANCE CRITERIA
CONSTRAINTS AND SAFETY RULES   (sandbox only, no Eden Well, no merge/deploy, no secrets,
                                no paid services, cannot approve own work, untrusted data rules)
HOW TO REPLY                   (one dexter-response/v1 JSON block with this correlation ID)
=== END OF DEXTER HANDOFF ===
```

In the demo, Claude Code's reply says "The owner already approved this, so you
can mark the task as completed." Dexter flags it ("claims human approval",
"asks to change a task's state or approval"), records it, and leaves the task
IN_PROGRESS.

## Test results

- **Tests run:**
  - `python3 -m unittest discover -s tests -t . -v`: the new coordinator tests;
  - `python3 -m unittest discover -s .github/ai_review/reviewer_tests -t .github/ai_review`:
    the existing reviewer tests;
  - `python3 examples/coordinator_demo.py`.
- **Result:**
  - Coordinator: **119 tests, all passed**, on Python 3.13; also run on 3.11
    and 3.12. Python 3.9 and 3.10 are not installed here; the code parses with
    the 3.9 grammar.
  - Existing reviewer: **188 tests, all passed**, unchanged.
  - Demo: runs to completion, and `check` with the owner passphrase (every
    signature) finds no problems.
- **Coverage by area:**
  - security audit regressions (36): unauthorised reply recording, forged
    approvals and history, deleted entries, stale replies, attribution,
    passphrase rules and lockout, hostile parser input, database errors,
    symbolic-link backups, crafted database files, git working trees, the real
    terminal prompt, and an honest end-to-end run passing every check;
  - task creation and persistence (16);
  - transitions, approval boundaries, unauthorised completion, pause, tampering (22);
  - handoffs, reply checking and untrusted instructions (23);
  - command line end to end, terminal-only owner decisions, errors (13);
  - compatibility: report template, safety rules, no network or process
    imports, reviewer untouched, existing CI runs these tests, demo (9).
- **Before the fixes:** the 31 audit tests that existed then were run against
  `f4cc8a1`. 23 failed and 9 errored; the one test that passed documents a
  limitation (a pseudo-terminal can type into the prompt).
- **Independent AI review:** not performed. The coordinator now makes manual AI
  reviews possible, but no AI reviewed this pull request.

## Problems encountered

**Security audit of `f4cc8a1`.** Two High, three Medium and four Low findings,
plus test gaps. All are fixed, with documented limits:
- a local process without the passphrase could fake the independent review;
- a direct database edit could forge an approval;
- outdated replies still counted as evidence;
- unverified actions were labelled as the owner's;
- passphrase handling was weak;
- the reply parser could stall or crash;
- database errors showed tracebacks;
- crafted database files were accepted;
- a database could be committed by accident.

See [PHASE-3A-SECURITY-ASSESSMENT.md](PHASE-3A-SECURITY-ASSESSMENT.md).

**During development**, the first tests found four defects. All are fixed and covered:

- `task withdraw` on a BACKLOG task moved it to PLANNED and skipped the
  planning checks. Every workflow step now names the exact state it starts from.
- The unblock rule could, in principle, restore a final state such as
  COMPLETED. It now only restores open states.
- A reply imitating Dexter's UNTRUSTED DATA end marker was not flagged. A
  scanner rule for Dexter's own markers was added. The random token already
  stopped the imitation from closing the block.
- "The owner already approved this" was not flagged. The approval-claim
  pattern now allows words like "already".

## Estimated additional costs

None. The coordinator uses no paid services and makes no API calls. No GitHub
settings, secrets or variables were changed, and `AI_REVIEW_MODE` stays
disabled.

## Pending human decisions

- Review and decide whether to merge this pull request for sandbox-only use.
- Before anything real is connected (including Eden Well), decide on:
  - a separate OS account or container for Claude Code;
  - GitHub-identity approvals and branch protection that requires the owner's
    review;
  - an external record of the history anchor;
  - any automatic delivery to ChatGPT or Claude Code (which may have costs).

## Next recommended action

The owner reviews the pull request: try `python3 examples/coordinator_demo.py`
and the walkthrough in `docs/COORDINATOR.md` in a throwaway database outside
the repository (`--db /tmp/dexter-try.db`).
