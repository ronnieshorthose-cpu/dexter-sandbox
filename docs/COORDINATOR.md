# Dexter project coordinator (Phase 3A)

Dexter keeps track of software development work shared between the human
owner, ChatGPT and Claude Code. It stores projects and tasks, enforces who may
do what, prepares structured handoff messages for the two AI assistants,
records their replies, and writes plain-English progress reports.

**In this phase Dexter is local and manual:**
- It runs on your computer and stores everything in one SQLite file.
- It never contacts ChatGPT, Claude Code, GitHub or any other service. You copy
  each handoff message to the assistant yourself and paste the reply back.
- Every owner action is a **SIMULATED approval**, checked with a local
  passphrase. It is not secure authentication, and it is not connected to
  GitHub: approving or completing a task in Dexter does not merge, deploy or
  change anything anywhere.

For the security design, what is enforced today, and what needs separate
operating-system accounts, external services or GitHub identity, see
[COORDINATOR-SECURITY.md](COORDINATOR-SECURITY.md).

Contents: [Install and run](#install-and-run) ·
[Roles](#roles) · [Task states](#task-states) ·
[Create a project](#create-a-project) · [Submit a task](#submit-a-development-task) ·
[Exchanging work between ChatGPT and Claude Code](#how-chatgpt-and-claude-code-exchange-work-through-dexter) ·
[How the owner approves work](#how-the-human-owner-approves-work) ·
[Checking the records](#checking-the-records) ·
[Recovering from errors](#recovering-from-errors) ·
[Working vs planned](#working-and-planned-features) ·
[Security limitations](#security-limitations) · [Demo](#demonstration)

## Install and run

You need Python 3.9 or newer (tested with 3.11, 3.12 and 3.13) with the
standard `hashlib.scrypt` function, which normal CPython builds include.
Nothing else is installed: the coordinator uses only Python's standard library.

```sh
git clone https://github.com/ronnieshorthose-cpu/dexter-sandbox.git
cd dexter-sandbox
python3 -m dexter_coordinator --help
```

Run every command from the repository folder. To keep typing short you can
define an alias for your terminal session:

```sh
alias dexter="python3 -m dexter_coordinator"
```

The rest of this guide writes `dexter` for `python3 -m dexter_coordinator`.

**Where data is stored.** By default Dexter uses `~/.dexter/coordinator.db`.
New databases are created readable only by your account (mode 600, in a
folder with mode 700). Use another file with `--db FILE` before the command,
or by setting `DEXTER_DB`, for example a throwaway database to try things out:

```sh
dexter --db /tmp/dexter-try.db project list
```

Dexter refuses to create a database or a backup inside a git working tree
(any folder with a `.git` above it), so approval records are never committed
by accident. The repository's `.gitignore` also ignores `*.db` files.
`--allow-inside-git-repo` overrides this; it is not recommended.

**First run.** Set up the database and choose the owner passphrase (at least
12 characters):

```sh
dexter init
```

Whoever runs `init` first becomes the owner as far as Dexter can tell. Do it
yourself, in your own terminal, before any AI tool can reach the database.

To run the tests:

```sh
python3 -m unittest discover -s tests -t . -v
```

## Roles

`dexter roles` prints this list.

| Role | ID | What it does in Dexter |
|---|---|---|
| Human Owner | `human_owner` | Sets goals and priorities. Approves tasks before work starts, accepts or returns finished work, cancels tasks, resumes projects, and records every AI reply. Approves merges, deployments and expenses outside Dexter. |
| Dexter | `dexter` | Records requests as tasks, assigns them, tracks states, dependencies and blockers, prepares handoffs, writes reports, and asks the owner at each checkpoint. |
| ChatGPT | `chatgpt` | Architect and reviewer: reviews requirements before approval, and independently reviews Claude Code's implementation reports. Recommends only. |
| Claude Code | `claude_code` | Developer: implements approved tasks on a branch, runs tests, documents changes, opens pull requests, and reports back. |

Tasks can only be assigned to `chatgpt` or `claude_code`. The AI roles never
act inside Dexter. Their replies are recorded by the owner as data, and they
cannot change a task's state or grant an approval.

**Who is recorded as acting.** Every history entry names who made it, and how
sure Dexter is:

| History label | Meaning |
|---|---|
| `OWNER (passphrase, signed)` | Someone typed the owner passphrase. The entry is signed with the owner's key. |
| `DEXTER (operator unverified)` | A coordinator step (planning, starting, preparing a handoff...). Anyone who can run the command line can do these, so the person is not known. |
| `LOCAL OPERATOR (unverified)` | Pausing a project, setting up the passphrase, or a wrong passphrase. Done at this computer by someone Dexter did not identify. |

A reply's sender ("claims to be from ChatGPT") is always shown as a claim.
Nobody can prove who wrote a pasted reply; the owner's signature only vouches
that the owner recorded that exact text.

## Task states

```
BACKLOG -> PLANNED -> AWAITING_APPROVAL -> APPROVED -> IN_PROGRESS -> IN_REVIEW -> READY_FOR_HUMAN -> COMPLETED
                                       \-> REJECTED            ^           |
                                                               +-----------+  (changes requested)
Any open state -> BLOCKED -> back to the state it was in
Any open state -> CANCELLED
```

| Move | Who | Command | Conditions |
|---|---|---|---|
| BACKLOG → PLANNED | Dexter | `task plan` | An assigned AI role and at least one acceptance criterion |
| PLANNED → BACKLOG | Dexter | `task unplan` | |
| PLANNED → AWAITING_APPROVAL | Dexter | `task submit` | Same as planning |
| AWAITING_APPROVAL → PLANNED | Dexter | `task withdraw` | To change the task before the owner decides |
| AWAITING_APPROVAL → APPROVED | **Owner** | `task approve` | Passphrase. The banner shows exactly what you approve |
| AWAITING_APPROVAL → REJECTED | **Owner** | `task reject` | Passphrase and reason |
| APPROVED → IN_PROGRESS | Dexter | `task start` | Every dependency is COMPLETED, and the task's records match the owner's signed approval |
| IN_PROGRESS → IN_REVIEW | Dexter | `task review` | An owner-recorded `done` report from the assignee, answering a handoff prepared since the task entered IN_PROGRESS. For Claude Code: at least one passing test and no failing tests |
| IN_REVIEW → IN_PROGRESS | Dexter | `task request-changes` | Reason |
| IN_REVIEW → READY_FOR_HUMAN | Dexter | `task ready` | An owner-recorded review addressed to the *other* AI role, answering a handoff prepared since the task entered IN_REVIEW, recommending acceptance, with no critical or major findings |
| READY_FOR_HUMAN → COMPLETED | **Owner** | `task complete` | Passphrase |
| READY_FOR_HUMAN → IN_PROGRESS | **Owner** | `task return` | Passphrase and reason |
| open → BLOCKED | Dexter | `task block` | Reason |
| BLOCKED → previous state | Dexter | `task unblock` | |
| open → CANCELLED | **Owner** | `task cancel` | Passphrase and reason |

COMPLETED, REJECTED and CANCELLED are final. A task's name, description,
priority, assignee, criteria and dependencies can only change in BACKLOG or
PLANNED, so an approval always covers exactly what gets built. The approval
records a fingerprint of that content.

## Create a project

```sh
dexter project create "Pocket Library" --description "A command-line tool for a small library"
dexter project list
dexter project status PRJ-001
```

Projects get IDs like `PRJ-001`. Anyone at this computer can stop all
coordinator activity on a project at once; only the owner can resume it:

```sh
dexter project pause PRJ-001 --reason "Rethinking priorities"   # no passphrase: pausing only stops work
dexter project resume PRJ-001                                    # owner passphrase
```

While a project is paused, Dexter will not plan, start, review or unblock
tasks, or prepare handoffs. The owner can still approve, reject, complete,
return and cancel, and tasks can still be blocked.

## Submit a development task

```sh
dexter task create PRJ-001 "Book catalogue model" \
  --description "Store books (title, author, ISBN) in a JSON file and list them sorted by title." \
  --criterion "Books can be added and listed" \
  --criterion "Unit tests cover add and list" \
  --assign claude_code --priority high

dexter task create PRJ-001 "Borrow and return" --description "Lend and return books" \
  --criterion "A lent book cannot be lent twice" --assign claude_code --depends-on TASK-0001

dexter task show TASK-0001        # everything about a task, plus the next step
dexter task list --project PRJ-001
dexter task plan TASK-0001
dexter task submit TASK-0001      # now waiting for the owner
```

Other editing commands (BACKLOG or PLANNED only): `task edit`,
`task add-criterion`, `task assign`, `task depend`, `task undepend`.
Dependencies must be in the same project and cannot form a loop.

Every command prints the next step. `dexter task show TASK-ID` always ends
with a "Next step" line.

## How ChatGPT and Claude Code exchange work through Dexter

The assistants never talk to each other or to Dexter directly. Each step is a
**handoff**: Dexter prepares a message, you copy it, and you paste the reply
back.

```
          handoff (you copy it)                       reply (you paste it back and record it)
Dexter ──────────────────────────────> ChatGPT / Claude Code ──────────────────────────────────────> Dexter
```

1. **Prepare the handoff.** `dexter handoff create TASK-0001`. Dexter picks
   the action and the recipient from the task's state:

   | Task state | Action | Goes to |
   |---|---|---|
   | BACKLOG, PLANNED, AWAITING_APPROVAL | `architecture_review` (advice before the owner decides) | ChatGPT |
   | IN_PROGRESS | `implement` | the assigned role |
   | IN_REVIEW | `review` | the other AI role (never the one that did the work) |

   There is no `implement` handoff before the owner has approved the task and
   Dexter has started it.

2. **Read it, then copy it.** The message sits between `COPY EVERYTHING
   BELOW/ABOVE THIS LINE` markers (or use `--save FILE`).
   - **Contents:** the project and task IDs, the sender and recipient roles, the
     objective, context, files and references (`--ref`), acceptance criteria, the
     safety rules, the requested action, the exact reply format, a timestamp and
     a correlation ID.
   - **Earlier AI replies** included in the context sit between
     `BEGIN/END UNTRUSTED DATA <random token>` markers.
   - **Read before sending.** Anyone who can run Dexter's command line can add
     notes to a handoff (`--context`), so read it before you send it.

3. **Paste it into ChatGPT or Claude Code.** Optionally confirm that you did:
   `dexter handoff mark-sent HO-0001` (owner passphrase). Dexter never claims
   a message was delivered, because it has no way to check.

4. **Record the reply.** Save the assistant's whole reply to a file and run:

   ```sh
   dexter handoff record HO-0001 --file reply.txt
   ```

   **What Dexter checks first:** the reply must contain exactly one JSON block
   in the `dexter-response/v1` format shown in the handoff, and the check is
   strict.
   - The correlation ID, task ID and claimed sender must match the handoff.
   - The outcome must be valid for the action.
   - Unknown fields (for example `"approved": true`) are refused, as are
     repeated fields, deeply nested JSON, control characters and oversized text.
   - The handoff must be **current**. A reply is refused if the task has
     changed state, or been edited, since the handoff was prepared. For
     example, a review of work that was later redone is refused. Prepare a new
     handoff instead.

   **What you see before confirming:** the claimed sender, the outcome, the
   summary, and warnings for text that looks like instructions or claims ("the
   owner approved this", "merge the PR", "ignore previous instructions",
   secrets, Eden Well, imitations of Dexter's own markers).

   **Then you confirm with the owner passphrase.** Only confirm a reply you
   pasted from that assistant yourself. **Recording a reply never changes a
   task's state.**

   If an assistant will not use the format, you can keep its reply with
   `--unstructured`. It is stored for the record, but Dexter cannot check it,
   so it does not count as evidence for any step.

5. **Move the task on** with the Dexter commands in the state table, for
   example `task review` once Claude Code reports `done` with passing tests,
   or `task ready` once ChatGPT recommends acceptance.

Each handoff accepts one reply. To ask again, create a new handoff.

## How the human owner approves work

**Owner actions:**
- Approve or reject a task before work starts.
- Record each AI reply, and confirm that a handoff was sent.
- Accept finished work (`task complete`) or send it back (`task return`).
- Cancel a task, or resume a paused project.
- Change the passphrase.

**The two main checkpoints:**
1. **Before work starts:** `dexter task approve TASK-0001` (or
   `task reject TASK-0001 --reason "..."`). The banner shows the task's
   assignee, priority, dependencies, description and criteria: that is exactly
   what your approval covers.
2. **When work is finished:** after the independent review, the task is
   READY_FOR_HUMAN. Check the pull request and test results yourself, then run
   `dexter task complete TASK-0001`, or send it back with
   `task return TASK-0001 --reason "..."`.

**Every owner action:**
- shows a banner, asks for the passphrase, and is recorded with the label
  **SIMULATED human approval**;
- is signed with the owner's key (an HMAC-SHA256 signature over the history
  entry);
- first re-checks every stored record and every owner signature, and refuses
  to run if anything has been tampered with.

**The passphrase:**
- **Strength and storage.** It must be at least 12 characters. It is stretched
  with scrypt (salted, 32 MiB of memory per check), and only a verifier is
  stored.
- **The signing key.** The owner's signing key is stored wrapped with a key
  derived from the passphrase.
- **Changing it.** Use `dexter owner change-passphrase`, which needs the old
  one; the same signing key is re-wrapped, so old signatures stay valid.
- **Wrong attempts.** Two wrong passphrases in a row are allowed. After that,
  owner actions are refused for a growing period (2, 4, 8... seconds, up to 15
  minutes). Wrong attempts are recorded in the history.
- **How it is typed.** It is read from an interactive terminal; input piped in
  from a file or another program is refused. **This is not an authentication
  boundary.** A program can open a pseudo-terminal and type into the prompt
  (the tests do exactly that). The passphrase is the only real check, so never
  give it to an AI assistant or store it where one can read it.

**Completing a task records your acceptance in Dexter only.** Merging a pull
request on GitHub, deploying, and spending money remain separate actions that
only you take, outside Dexter.

## Checking the records

```sh
dexter check            # history chain, stored records vs history, file permissions
dexter check --owner    # also checks every owner signature (passphrase)
```

**`check` looks for:**
- history entries that were changed, removed or re-ordered;
- tasks whose stored state, approval or acceptance disagrees with their
  replayed history;
- replies or handoffs that differ from what was recorded;
- human-only decisions made by anyone else;
- unexpected database structure;
- a database file other users can read.

**`check --owner`** also verifies every owner signature. That catches records
forged by someone who rewrote the history but does not know the passphrase.

**The history anchor.** Both commands print an anchor (the newest entry
number and a fingerprint). Note it somewhere off this computer. A later anchor
that is *older* than one you noted means the file was replaced with an older
copy, which nothing else can detect.

## Progress reports and history

```sh
dexter report PRJ-001                          # plain-English report (sections of DEXTER-REPORT-TEMPLATE.md)
dexter report PRJ-001 --save ~/pocket-report.md
dexter task history TASK-0001                  # every change to one task
dexter history --project PRJ-001               # every change in a project
```

Reports say where each fact comes from. Test results are what a reply
*claiming* to be from an assistant said; Dexter did not run them.

## Recovering from errors

| Problem | What to do |
|---|---|
| `Error: ...` after a command | Nothing was changed: every command saves completely or not at all. (The one exception is a wrong owner passphrase, which is recorded in the history.) Read the message; it says what is missing. |
| "the database is busy" | Another Dexter command is using the file. Wait and try again. |
| "too many wrong passphrases; wait N seconds" | Wait the time shown. Each further wrong passphrase doubles the wait, up to 15 minutes. |
| A reply is rejected | Read the reason. A wrong correlation ID means the reply belongs to a different handoff. "Prepared before ... entered its current state" or "was changed after" means the handoff is out of date: run `handoff create` and send the new message. To keep a reply that cannot be fixed, use `--unstructured`. |
| You recorded the wrong reply | Replies cannot be deleted (they are part of the record). Create a new handoff and record the right reply there. Only replies to handoffs prepared in the task's current state count. |
| A task needs changes after it was submitted | `dexter task withdraw TASK-ID`, edit it, then `task submit` again. After approval its content is locked: ask the owner to cancel it and create a new task. |
| An assistant reports it is blocked | `dexter task block TASK-ID --reason "..."`, resolve the problem, then `task unblock`. |
| "needs the passphrase typed into an interactive terminal" | Run the owner command yourself in a real terminal, not through a script. |
| "the stored records fail Dexter's checks, so owner actions are refused" | Something changed the database outside Dexter. Run `dexter check --owner` to see what. Restore the newest backup whose `check --owner` passes, or start a new database. Do not keep working in a file that fails its checks. |
| Your passphrase suddenly stops working | Either you mistyped it, or the owner record was replaced. Treat it as tampering: run `dexter check` and restore from a backup. |
| Forgotten owner passphrase | It cannot be recovered. Keep the old database for reference and start a new one with `--db` and `init`. |
| "database version ... cannot be upgraded" | The file was made by the first Phase 3A test version. Start a new database. |
| "does not have the expected Dexter structure" | The file has extra tables, triggers or views, or changed tables. Do not use it; restore a backup. |
| "refusing to create a database inside a git working tree" | Choose a path outside the repository, such as `~/.dexter/coordinator.db`. |
| Backups | `dexter backup ~/dexter-backup-2026-10-26.db` makes a consistent, private copy. It never overwrites, never follows symbolic links, and refuses git working trees. To restore, use the copy with `--db`, and run `check --owner` on it first. |

## Working and planned features

**Working now (Phase 3A):**
- **Storage:** a local SQLite file holding projects, tasks, priorities,
  dependencies, acceptance criteria, assignments, approvals, test results,
  review findings and a timestamped, hash-chained activity history, with owner
  entries signed.
- **Workflow rules:** enforced task states and transitions, including
  dependency, evidence, freshness and independent-review checks, blocking,
  cancellation and project pause.
- **Owner actions:** SIMULATED approvals with a local passphrase (scrypt,
  lockout, signatures), entered at the terminal, including recording replies.
- **Handoffs:** manual handoffs in `dexter-handoff/v1`, with strict
  `dexter-response/v1` reply checking and flagging of untrusted instructions.
- **Tools:** a command-line interface, progress reports, history, `check`
  (including `--owner`) and `backup`.
- **Tests:** automated tests in `tests/`, run by the existing **Tests** workflow.

**Planned, not built:** see the "Needs..." tables in
[COORDINATOR-SECURITY.md](COORDINATOR-SECURITY.md). In short:
- **Separate accounts:** running Dexter under its own operating-system account,
  out of reach of Claude Code.
- **External services:** an external, append-only record of the history
  anchor, and automatic, authenticated delivery to the assistants (which needs
  owner approval of any cost first).
- **GitHub identity:** approvals checked through the owner's GitHub identity,
  and links from tasks to real branches, pull requests and CI results, so
  Dexter can check results itself.
- **Eden Well:** connecting to it is not permitted in this phase.

## Security limitations

Read [COORDINATOR-SECURITY.md](COORDINATOR-SECURITY.md) for the full list.
The most important:

- **Approvals are simulated.**
  - Anyone who knows the passphrase is the owner as far as Dexter can tell.
  - Anyone who can read the database file can try passphrases offline (one
    scrypt run per guess).
  - Anyone who can write the file can delete it, replace it with an older copy,
    or replace the owner record. The real owner then notices that their
    passphrase stops working, or that the anchor went backwards.
  - A local signature cannot stop tampering by someone who controls both the
    file and the passphrase.
- **The terminal check is not authentication.** A program that opens a
  pseudo-terminal can type into the prompt. Only the passphrase protects owner
  actions.
- **Coordinator steps are not authenticated.** Anyone who can run the command
  line can plan, start, block, request changes and prepare handoffs (including
  adding notes to them). They cannot record replies, approve, complete or
  resume without the passphrase.
- **Senders are claims.** Nothing proves which assistant wrote a reply.
- **Instruction flagging is a heuristic.** It catches common phrasings, not
  every possible one. The real protection is that Dexter never executes or
  acts on reply text.
- **Reported results are not verified.** Check the pull request and CI yourself
  before completing a task.
- **The assistants act outside Dexter.** The safety rules in each handoff ask
  them to stay in this sandbox; Dexter cannot enforce what they do in their
  own tools.

## Demonstration

`examples/coordinator_demo.py` runs a fictional project ("Pocket Library", a
book-lending tool) from request to owner acceptance in a throwaway database:

```sh
python3 examples/coordinator_demo.py
```

**What it shows:**
- an architecture review by ChatGPT;
- a SIMULATED owner approval;
- a dependency stopping a task from starting early;
- the implementation handoff to Claude Code;
- a reply that falsely claims the owner already approved it (flagged and
  ignored);
- the independent review and the owner's acceptance;
- the progress report, the history, and a full signature check.

**Demo only:** the demo writes its passphrase into the script so it can run
unattended. The ChatGPT and Claude Code replies are pre-written; nothing is
sent anywhere.
