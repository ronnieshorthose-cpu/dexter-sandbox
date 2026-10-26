# Dexter coordinator: security design and limits

This document describes how the Phase 3A coordinator protects owner decisions,
what it can and cannot enforce on its own, and how a future deployment keeps
Claude Code away from the owner's approval database, passphrase and signing
key. The audit that led to this design, and how each finding was fixed, is in
[PHASE-3A-SECURITY-ASSESSMENT.md](PHASE-3A-SECURITY-ASSESSMENT.md).

## What needs protecting

| Asset | Where it lives | Why it matters |
|---|---|---|
| Owner passphrase | Only in the owner's head | It is the one thing that proves an owner action |
| Passphrase verifier and wrapped signing key | `owner` table in the database file | With the file, an attacker can try passphrases offline; with the passphrase too, they can sign anything |
| Approval records and history | `tasks`, `events`, `responses`, `handoffs` tables | They decide what work may start and what the owner is shown |
| Handoff messages | Database, then the owner's clipboard | They are what the assistants are asked to do |

## Trust zones

```
 OWNER ZONE (the owner's OS account)            AGENT ZONE (Claude Code)           CHATGPT (web)
 ─────────────────────────────────────          ────────────────────────          ─────────────
 ~/.dexter/coordinator.db  (mode 600)           repository checkout only          no local access
 owner passphrase (typed, never stored)         no access to ~/.dexter            sees only what
 dexter command line                            no Dexter command line            the owner pastes
        │                                              ▲                                ▲
        │  owner copies handoff text ──────────────────┴────────────────────────────────┘
        │  owner pastes replies back, reads them, confirms with the passphrase
        ▼
 signed history entries
```

**The rule for every deployment:** Claude Code must never run under the
owner's operating-system account, and must never be able to read or write the
Dexter database, its backups, or the owner's terminal.
- If it can *read* the file, it can try passphrases offline.
- If it can *write* the file, it can delete or roll back records.
- If it can *drive the owner's terminal* and learns the passphrase, it can act
  as the owner.

## Safeguards by what enforces them

### Enforced today by the code (no extra setup)

| Safeguard | Where |
|---|---|
| No code path lets an AI reply change a task's state, approve, merge or deploy; replies are stored as data | `coordinator.record_response` |
| Owner actions (approve, reject, complete, return, cancel, resume, record a reply, mark a handoff sent, change the passphrase) need the passphrase | `coordinator._owner_key` |
| Passphrase stretched with scrypt (N=2^15, r=8, p=1), at least 12 characters, only a verifier stored | `auth.py` |
| Growing lockout after repeated wrong passphrases (up to 15 minutes); wrong attempts recorded | `coordinator._authenticate` |
| Every owner history entry signed with HMAC-SHA256 using a random key that is stored wrapped under the passphrase | `store.add_event`, `auth.py` |
| Owner actions refuse to run if any record fails a check or any owner signature is invalid | `coordinator._owner_key` |
| Each task's history is replayed and compared with its stored state, approval and acceptance; evidence and approvals must match signed owner entries | `coordinator._task_problems`, `_structural_problems` |
| Approvals bound to a fingerprint of the task content; content locked after submission | `_content_hash`, `_require_editable` |
| Replies only count if their handoff was prepared in the task's current state and for its current content | `_check_reply_can_be_recorded`, `_latest_response` |
| Strict reply format; linear-time parsing; nesting, size and control-character limits | `handoff.py` |
| Unverified actions labelled `dexter (operator unverified)` or `local_operator`, never as the owner; reply senders shown as claims | `coordinator.py`, `cli.py` |
| Database structure checked on every open; extra tables, triggers or views refused; `trusted_schema` off | `store.Store` |
| New databases and backups created owner-only (mode 600), never through symbolic links, never over existing files, never inside a git working tree; `.gitignore` covers `*.db` | `store.py`, `.gitignore` |
| `check` warns when the database is readable by other users or owned by another account | `store.permission_warnings` |
| No network, process, git or GitHub code in the package | `tests/test_coordinator_compat.py` |

### Needs separate operating-system accounts (setup, not code)

These are what actually keep Claude Code away from the owner's material. The
code cannot enforce them on a computer where everything runs as one user.

| Safeguard | How |
|---|---|
| Claude Code cannot read or write the database, backups or keys | Run Claude Code as a different OS user, or in a container or VM with no access to the owner's home folder. Keep the database in `~/.dexter` (mode 700 folder, mode 600 file) under the owner's account. `dexter check` warns if the file is readable by others or owned by another account. |
| Claude Code cannot type into the owner's terminal | Do not let AI tools control the terminal session where the owner runs Dexter. The terminal check stops piped input only; a program with a pseudo-terminal can type into the prompt. |
| The passphrase never reaches an AI tool | Type it only into Dexter's prompt. Never paste it into a chat, a file in a repository, or an environment variable. |
| Backups are out of reach too | Store backups in the owner's account or offline media, not in folders an agent can read. |

### Needs external services

| Safeguard | Why it cannot be done locally |
|---|---|
| Detect the whole database being replaced by an older copy | Needs a record outside the attacker's reach. Today the owner notes the history anchor printed by `check`. Later: send the anchor to an append-only service, or commit it to a separate private repository the agent cannot write. |
| Stronger owner authentication (passkey, hardware key, OS keychain) | Python's standard library has no hardware-key support; this needs an OS keychain or a security key. |
| Confirmed delivery to ChatGPT or Claude Code, and proof of which assistant wrote a reply | Needs authenticated API channels. Paid use must be approved by the owner first. |

### Needs GitHub identity verification

| Safeguard | How |
|---|---|
| Approvals that someone other than the owner cannot forge | Express approvals as GitHub pull request reviews by the owner's account, and have Dexter verify them through the GitHub API. |
| Merges only after the owner's approval | Branch protection on `main` requiring an approving review from the owner (or a code owner) and passing checks. Today the "Protect Dexter Main" ruleset requires 0 approvals. |
| Test results Dexter can trust | Read CI results for the pull request from GitHub instead of the assistant's report. |
| Signed commits | Require signed commits so work can be attributed to an account. |

## Limits of local-file security

Stated plainly, because the earlier version of these documents overstated them:

- **The passphrase is the only real check.** Whoever knows it is the owner as
  far as Dexter can tell.
- **Offline guessing.** Someone who can read the database file can try
  passphrases offline. scrypt makes each guess cost 32 MiB of memory and about
  0.1 seconds on the machine used for testing. That slows guessing, especially
  on GPUs, but cannot stop it for a weak passphrase.
- **Writing the file.** Someone who can write the database file can:
  - delete it, or replace it with an older copy (only the anchor kept
    elsewhere shows this);
  - replace the owner record with their own. The real owner's passphrase then
    stops working, and `check --owner` with the attacker's passphrase reports
    every older owner entry as invalid, so this is visible but not prevented.
  - change unsigned records. `check` and every owner action detect this;
    unauthenticated coordinator steps detect it only for the task they move.
- **The file and the passphrase together.** A local signature does not prevent
  tampering by someone who controls both the database and the passphrase (and
  so the signing key). They can forge any record.
- **The terminal check.** It refuses piped input; it does not prove a person
  is typing.
- **Unauthenticated coordinator steps.** Planning, starting, blocking,
  requesting changes and preparing handoffs can be run by anyone with the
  command line. They can delay or disrupt work, and add notes to a handoff,
  but cannot record evidence or make owner decisions.
- **Reply authorship.** Nothing proves which assistant wrote a reply. The owner
  vouches only that they recorded it.

## Before Dexter is connected to anything real (including Eden Well)

1. Run Claude Code under a separate account or container with no access to the
   owner's Dexter files (see above), and verify it with `dexter check` on the
   owner side.
2. Move approvals of merges onto GitHub identity: owner-only approving reviews
   and branch protection that requires them.
3. Keep the history anchor outside the computer, or automate that with an
   append-only service.
4. Replace hard-coded sandbox rules in handoffs with a per-project list of
   allowed repositories, approved by the owner.
5. Get an independent security review of the coordinator.
