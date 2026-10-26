# Dexter Phase 3A: security assessment of the project coordinator

**Date:** 2026-10-26
**Scope:** the coordinator in `dexter_coordinator/`, its tests, demo and documents, as audited on branch
`dexter/phase-3a-project-coordinator` at commit `f4cc8a1`, and the fixes that followed.

> **This is a self-assessment.** The same kind of agent that wrote the
> coordinator audited it and fixed it. It has the usual weaknesses of a
> self-review and is no substitute for an independent audit.

## How the audit was done

The code was read in full, not just the documentation. Each suspected weakness
was then reproduced against throwaway databases outside the repository:
- driving the Python API and the command line as an AI agent would;
- editing the SQLite file directly;
- timing hostile inputs;
- driving the passphrase prompt through a pseudo-terminal.

The audit verdict was **FIX BEFORE MERGE**. The owner approved fixing all
nine findings plus the test gaps.

Every finding has regression tests in `tests/test_coordinator_audit.py`. The
tests were written first and run against `f4cc8a1`: **31 tests, 23 failures
and 9 errors**. The only test that passed there documents a limitation (a
pseudo-terminal can type into the prompt). Some errors came from the changed
API itself; for example, the audited `record_response` took no passphrase.

## Findings and remediation

| ID | Severity | Finding | Status |
|---|---|---|---|
| H-1 | High | A local process without the passphrase could record replies, fake the "independent" review and move a task to READY_FOR_HUMAN | Fixed |
| H-2 | High | One edit to the database file created an approval that `start` accepted and `check` missed; deleted history and edited replies went unnoticed | Fixed, with documented limits |
| M-1 | Medium | Replies to handoffs prepared before the task changed still counted as evidence | Fixed |
| M-2 | Medium | Unauthenticated actions were recorded as the human owner's | Fixed |
| M-3 | Medium | Weak passphrase handling and overstated claims about the terminal check | Fixed, with documented limits |
| L-1 | Low | Quadratic fence matching (40 s for a 200,000-character reply) and an uncaught RecursionError on nested JSON | Fixed |
| L-2 | Low | Database errors shown as tracebacks; backup followed dangling symlinks; duplicate-reply check not atomic | Fixed |
| L-3 | Low | A crafted database with triggers or views was accepted | Fixed |
| L-4 | Low | Documentation suggested a database inside the repository; nothing stopped it being committed | Fixed |
| T-1 | Medium | Tests did not cover the findings above | Fixed |

### H-1: Owner authorization for replies; senders are claims
**Before:** `record_response` and `mark_handoff_sent` needed no passphrase. A
reply's `from_role` was taken as the sender's identity.

**Fix:**
- **Owner actions.** Recording a reply and confirming a handoff was sent are
  now owner actions: passphrase, signed history entry, full integrity check
  first.
- **Show before confirming.** The command line first checks the reply, then
  shows the owner the claimed sender, outcome, summary and warnings before
  asking for the passphrase.
- **Senders are claims.** Every view says "claims to be from ...". The task
  record has `review_claimed_by` / `tests_claimed_by` and
  `reviewer_verified: False`.
- **Independence.** Reviewer independence is enforced on the handoff's
  addressee, which Dexter chooses, never on what a reply says about itself.

**Tests:** `H1OwnerMustRecordReplies` (4 tests).

**Remaining:** unauthenticated coordinator steps (plan, start, block, request
changes, prepare handoffs) can still be run by anyone with the command line.
They cannot create evidence. Nothing can prove which assistant wrote a reply.

### H-2: Approval integrity
**Before:** `start` trusted the approval field in the task row. `check` only
compared that field with the task's state and hashed the history chain without
a key.

**Fix:**
- **Signed owner entries.** Each owner history entry carries an HMAC-SHA256
  signature over the entry and its link to the previous entry.
  - The key is a random 32-byte signing key, stored wrapped (XOR) with a key
    derived from the passphrase through scrypt.
  - Changing the passphrase re-wraps the same key.
  - Non-owner entries must not carry a signature.
- **History replay.** Each task's history is replayed and checked:
  - every move must follow the transition rules and be made by its allowed
    actor;
  - the result must match the stored state, `state_seq` and blocked state;
  - the stored approval and acceptance must point at the owner's signed
    decision entry, with the same content fingerprint.
- **Recorded content.** Replies must match a SHA-256 digest held in the owner's
  signed "response recorded" entry. Handoff messages must match the digest in
  their creation entry.
- **Where it is enforced.**
  - Coordinator steps that depend on approval or evidence (`start`,
    `send_to_review`, `mark_ready`) refuse to move a task whose records fail
    these checks.
  - Every owner action re-runs all checks *and* verifies every owner
    signature, and refuses to act on tampered records.
  - `check` runs the structural checks; `check --owner` also verifies
    signatures.
  - `check` prints a history anchor to keep off the machine.

**Tests:** `H2ApprovalIntegrity` (6 tests) and `T1OwnerSetupAndSignatures`
(5 tests). These include a forged approval field, a forged and consistently
re-chained history entry, deleted newest entries, a state that disagrees with
the history, an edited reply, signature presence, an honest full workflow, a
passphrase change, and a replaced owner record.

**Remaining (documented in COORDINATOR-SECURITY.md):**
- Someone with the file and the passphrase can forge anything; a local
  signature cannot prevent that.
- Rolling the whole file back to an older copy is only visible through the
  anchor kept elsewhere.
- Replacing the owner record is detected (the real passphrase stops working)
  but not prevented.
- Unauthenticated steps check structure, not signatures, so a consistently
  forged history passes them until the next owner action or `check --owner`.

### M-1: Stale replies
**Before:** a reply counted if it was recorded after the task entered its
current state, even if its handoff was older.

**Fix:**
- **Recorded with each handoff:** the task's state entry and content
  fingerprint at the time it was prepared.
- **Refused replies:** recording is refused (inside the transaction) if the
  task has since changed state, been revised, or been blocked and unblocked.
- **Evidence:** the evidence queries count only replies to handoffs from the
  current state.

**Tests:** `M1StaleReplies` (3 tests): a review of work that was later redone,
an architecture review of an older task version, and a handoff from before a
block and unblock.

### M-2: No false owner attribution
**Before:** pausing, confirming a handoff was sent, setting up the passphrase,
and the "pasted in by the human" note were all recorded as the human owner.

**Fix:**
- **Owner entries.** Only passphrase-verified, signed entries are recorded as
  `human_owner`.
- **Unverified entries.** Pausing, owner set-up and wrong passphrases are
  `local_operator`. Coordinator steps are labelled
  `DEXTER (operator unverified)`.
- **Wording.** The reply note now says the sender is a claim. `init` states
  that whoever knows the passphrase is treated as the owner.

**Tests:** `M2NoFalseHumanAttribution` (2 tests).

### M-3: Passphrase handling and claims
**Before:** 8-character minimum, PBKDF2 with 310,000 iterations (about 70 ms
per offline guess), unlimited attempts, and documentation saying a script
"cannot make an owner decision".

**Fix:**
- **Hashing.** scrypt (N=2^15, r=8, p=1: 32 MiB of memory and about 0.1 s per
  guess on the test machine). Minimum 12 characters, NFC-normalised.
- **Lockout.** After two wrong passphrases in a row, owner actions are refused
  for 2, 4, 8... seconds, up to 15 minutes.
- **Documentation.** The docs now say the terminal check refuses piped input
  but is not an authentication boundary, because a program can open a
  pseudo-terminal. A test in `T1RealTerminalPath` does exactly that.

**Tests:** `M3PassphraseHandling` (3 tests), `T1RealTerminalPath` (1 test),
plus the command-line `init` and `owner change-passphrase` test.

**Remaining:** someone who can write the file can reset the lockout counter.
Offline guessing is slowed, not prevented.

### L-1: Reply parsing
**Fix:**
- **Fences.** Code fences are found line by line in linear time. The
  200,000-character input that took about 40 s now takes milliseconds.
- **Nesting.** JSON nesting is limited to 32 levels by a linear pre-scan (it
  ignores brackets inside strings), and any RecursionError becomes a
  validation error.

**Tests:** `L1ReplyParsing` (2 tests).

### L-2: Database errors, backups and duplicate replies
**Fix:**
- **Plain messages.** The command line turns every SQLite error into a plain
  message ("the database is busy..." for locks).
- **Safe backups.** Backups are created with
  `O_CREAT | O_EXCL | O_NOFOLLOW` and mode 600, so they never overwrite or
  follow a symbolic link. They report a missing folder plainly and refuse git
  working trees.
- **Atomic duplicate check.** The duplicate-reply check runs inside the
  writing transaction, and a UNIQUE-constraint race becomes a plain rule error.
- **Atomic workflow steps.** Every workflow step now reads, checks and moves
  inside one transaction.

**Tests:** `L2DatabaseErrors` (4 tests).

### L-3: Database structure
**Fix:**
- **Structure check.** On every open, the file's tables, indexes, triggers and
  views are compared with the expected schema, and any difference is refused.
- **Trusted schema off.** `PRAGMA trusted_schema = OFF`.
- **Versions.** The schema version moved to 2; databases from `f4cc8a1` are
  refused with an explanation. Only test databases existed.

**Tests:** `L3DatabaseStructure` (3 tests).

### L-4: Databases in repositories
**Fix:**
- **`.gitignore`.** It covers `*.db`, journals, `*.sqlite*` and `.dexter/`.
- **Creation refused in git trees.** New databases and backups are refused
  inside any git working tree, unless `--allow-inside-git-repo` is given.
- **Private files.** New database files are created with mode 600, before any
  data is written.
- **Documentation.** The docs now use `/tmp/dexter-try.db` and `~/...` paths.

**Tests:** `L4NoDatabasesInRepositories` (3 tests) and the permission test in
`T1OwnerSetupAndSignatures`.

**Note:** PR #3 adds its own `.gitignore`. Whichever of the two pull requests
merges second will need a simple merge of the two files.

### T-1: Test coverage
Added `tests/test_coordinator_audit.py` (36 tests), covering:
- every finding above;
- an honest end-to-end run passing every check, including signatures;
- the real passphrase prompt driven through a pseudo-terminal (POSIX only);
- the command-line `init` and passphrase change;
- file permissions.

Existing tests were updated where behaviour changed on purpose: the passphrase
is now required to record replies and mark handoffs sent, and the history
labels changed.

## Remaining risks

See [COORDINATOR-SECURITY.md](COORDINATOR-SECURITY.md) for detail and for
what needs separate OS accounts, external services or GitHub identity.

| ID | Risk | Mitigation today | Full fix needs |
|---|---|---|---|
| R-1 | An AI tool running as the owner's OS user can read the database (offline guessing) or write it (delete, roll back, replace the owner record) | Detection: replay checks, signatures, anchor, permission warnings | A separate OS account or container for Claude Code |
| R-2 | Whoever knows the passphrase is the owner | Strong-passphrase minimum, lockout, warnings | Hardware key or GitHub identity |
| R-3 | A rollback of the whole file to an older copy | History anchor noted outside the machine | An external append-only record |
| R-4 | Coordinator steps and handoff notes are unauthenticated | They cannot create evidence or make owner decisions; the owner reads handoffs before sending | A service running under the owner's account |
| R-5 | Reply authorship cannot be proven | Senders shown as claims; the owner vouches by recording | Authenticated delivery channels |
| R-6 | Reported test results are not verified | Labelled as claims; the owner checks the pull request | CI results read from GitHub |
| R-7 | Instruction flagging is heuristic | Dexter never acts on reply text | — |
| R-8 | Merges on GitHub are not gated by owner approval ("Protect Dexter Main" requires 0 approvals) | Human discipline | Branch protection requiring the owner's review |
| R-9 | Python 3.9 and 3.10, and non-Linux systems, were not tested | Code parses with the 3.9 grammar | CI runs on more versions |

## What did not change

- No network, process, git or GitHub code was added. Eden Well is not
  accessed, and is mentioned only in the safety rule and the scanner.
- `AI_REVIEW_MODE` stays disabled. No GitHub settings, secrets, variables or
  environments were created or changed. No paid calls were made.
- The advisory AI reviewer (`.github/`) and its tests were not modified.
