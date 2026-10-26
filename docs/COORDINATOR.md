# Dexter project coordinator (Phase 3A)

Dexter keeps track of software development work shared between the human
owner, ChatGPT and Claude Code. It stores projects and tasks, enforces who may
do what, prepares structured handoff messages for the two AI assistants,
records their replies, and writes plain-English progress reports.

**In this phase Dexter is local and manual:**
- It runs on your computer and stores everything in one SQLite file.
- It never contacts ChatGPT, Claude Code, GitHub or any other service. You copy
  each handoff message to the assistant yourself and paste the reply back.
- Every human owner decision is a **SIMULATED approval**, checked with a local
  passphrase. It is not secure authentication, and it is not connected to
  GitHub: approving or completing a task in Dexter does not merge, deploy or
  change anything anywhere.

Contents: [Install and run](#install-and-run) ·
[Roles](#roles) · [Task states](#task-states) ·
[Create a project](#create-a-project) · [Submit a task](#submit-a-development-task) ·
[Exchanging work between ChatGPT and Claude Code](#how-chatgpt-and-claude-code-exchange-work-through-dexter) ·
[How the owner approves work](#how-the-human-owner-approves-work) ·
[Recovering from errors](#recovering-from-errors) ·
[Working vs planned](#working-and-planned-features) ·
[Security limitations](#security-limitations) · [Demo](#demonstration)

## Install and run

You need Python 3.9 or newer (tested with 3.13). Nothing else is installed:
the coordinator uses only Python's standard library.

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

**Where data is stored.** By default Dexter uses `~/.dexter/coordinator.db`
(created with owner-only permissions). Use another file with `--db FILE`
before the command, or by setting `DEXTER_DB`:

```sh
dexter --db ./my-test.db project list
```

**First run.** Set up the database and choose the owner passphrase. This must
be done in an interactive terminal; the passphrase is typed, never passed as
an option.

```sh
dexter init
```

To run the tests:

```sh
python3 -m unittest discover -s tests -t . -v
```

## Roles

`dexter roles` prints this list.

| Role | ID | What it does in Dexter |
|---|---|---|
| Human Owner | `human_owner` | Sets goals and priorities. Approves tasks before work starts, accepts or returns finished work, cancels tasks, pauses and resumes projects. Approves merges, deployments and expenses outside Dexter. |
| Dexter | `dexter` | Records requests as tasks, assigns them, tracks states, dependencies and blockers, prepares handoffs, records replies, writes reports, and asks the owner at each checkpoint. |
| ChatGPT | `chatgpt` | Architect and reviewer: reviews requirements before approval, and independently reviews Claude Code's implementation reports. Recommends only. |
| Claude Code | `claude_code` | Developer: implements approved tasks on a branch, runs tests, documents changes, opens pull requests, and reports back. |

Tasks can only be assigned to `chatgpt` or `claude_code`. The AI roles never
act inside Dexter: their replies are recorded as data and cannot change a
task's state or grant an approval.

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
| AWAITING_APPROVAL → APPROVED | **Human owner** | `task approve` | Passphrase |
| AWAITING_APPROVAL → REJECTED | **Human owner** | `task reject` | Passphrase and reason |
| APPROVED → IN_PROGRESS | Dexter | `task start` | Every dependency is COMPLETED, and the task still matches what was approved |
| IN_PROGRESS → IN_REVIEW | Dexter | `task review` | A recorded `done` report from the assignee since the task entered IN_PROGRESS. For Claude Code: at least one passing test and no failing tests |
| IN_REVIEW → IN_PROGRESS | Dexter | `task request-changes` | Reason |
| IN_REVIEW → READY_FOR_HUMAN | Dexter | `task ready` | A recorded review from the *other* AI role since the task entered IN_REVIEW, recommending acceptance, with no critical or major findings |
| READY_FOR_HUMAN → COMPLETED | **Human owner** | `task complete` | Passphrase |
| READY_FOR_HUMAN → IN_PROGRESS | **Human owner** | `task return` | Passphrase and reason |
| open → BLOCKED | Dexter | `task block` | Reason |
| BLOCKED → previous state | Dexter | `task unblock` | |
| open → CANCELLED | **Human owner** | `task cancel` | Passphrase and reason |

COMPLETED, REJECTED and CANCELLED are final. A task's name, description,
priority, assignee, criteria and dependencies can only change in BACKLOG or
PLANNED, so an approval always covers exactly what gets built.

## Create a project

```sh
dexter project create "Pocket Library" --description "A command-line tool for a small library"
dexter project list
dexter project status PRJ-001
```

Projects get IDs like `PRJ-001`. The owner can stop all coordinator activity
on a project at any time, and resume it later:

```sh
dexter project pause PRJ-001 --reason "Rethinking priorities"   # no passphrase: pausing only stops work
dexter project resume PRJ-001                                    # passphrase
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
          handoff (you copy it)                       reply (you paste it back)
Dexter ──────────────────────────────> ChatGPT / Claude Code ──────────────────────────> Dexter
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

2. **Copy it.** The message sits between `COPY EVERYTHING BELOW/ABOVE THIS
   LINE` markers (or use `--save FILE`). It contains the project and task IDs,
   the sender and recipient roles, the objective, context, files and
   references (`--ref`), acceptance criteria, the safety rules, the requested
   action, the exact reply format, a timestamp and a correlation ID.
   Earlier AI replies included in the context (the implementation report in a
   review handoff, or review findings in a follow-up `implement` handoff) are
   placed between `BEGIN/END UNTRUSTED DATA <random token>` markers.

3. **Paste it into ChatGPT or Claude Code.** Optionally record that you did:
   `dexter handoff mark-sent HO-0001`. This records your statement only.
   Dexter never claims a message was delivered, because it has no way to
   check.

4. **Record the reply.** Save the assistant's whole reply to a file and run:

   ```sh
   dexter handoff record HO-0001 --file reply.txt
   ```

   The reply must contain exactly one JSON block in the `dexter-response/v1`
   format shown in the handoff. Dexter checks it strictly. The correlation ID,
   task ID and sender must match the handoff. The outcome must be valid for
   the action. Unknown fields (for example `"approved": true`), repeated
   fields, control characters and oversized text are refused with a plain
   message. Then Dexter scans the reply for text that looks like instructions
   or claims ("the owner approved this", "merge the PR", "ignore previous
   instructions", secrets, Eden Well, imitations of its own markers) and shows
   a warning. **Recording a reply never changes a task's state.**

   If an assistant will not use the format, you can keep its reply with
   `--unstructured`. It is stored for the record, but Dexter cannot check it,
   so it does not count as evidence for any step.

5. **Move the task on** with the Dexter commands in the state table, for
   example `task review` once Claude Code reports `done` with passing tests,
   or `task ready` once ChatGPT recommends acceptance.

Each handoff accepts one reply. To ask again, create a new handoff. Only replies
recorded after the task entered its current state count, so an old report
cannot be reused after changes were requested.

## How the human owner approves work

Owner decisions happen at two checkpoints, plus cancelling, pausing and
resuming:

1. **Before work starts:** `dexter task approve TASK-0001` (or
   `task reject TASK-0001 --reason "..."`).
2. **When work is finished:** after the independent review, the task is
   READY_FOR_HUMAN. Check the pull request and test results yourself, then run
   `dexter task complete TASK-0001`, or send it back with
   `task return TASK-0001 --reason "..."`.

Each decision shows a banner, asks for the owner passphrase, and is recorded
in the history with the label **SIMULATED human approval**. The passphrase:
- can only be typed into an interactive terminal (there is no option,
  environment variable or file for it, so a script or AI agent driving the
  command line cannot make an owner decision);
- is stored only as a salted PBKDF2-SHA256 hash;
- can be changed with `dexter owner change-passphrase`, which needs the old one.

Wrong passphrases are refused and recorded in the history.

**Completing a task records your acceptance in Dexter only.** Merging a pull
request on GitHub, deploying, and spending money remain separate actions that
only you take, outside Dexter.

## Progress reports and history

```sh
dexter report PRJ-001                 # plain-English report (sections of DEXTER-REPORT-TEMPLATE.md)
dexter report PRJ-001 --save report.md
dexter task history TASK-0001         # every change to one task
dexter history --project PRJ-001      # every change in a project
```

History lines are labelled by who acted: `HUMAN OWNER`, `DEXTER`,
`CHATGPT (AI, untrusted)` or `CLAUDE CODE (AI, untrusted)`, with
`[SIMULATED human approval]` on owner decisions. Reports say where each fact
comes from: test results are what an AI reported, and Dexter did not run them.

## Recovering from errors

| Problem | What to do |
|---|---|
| `Error: ...` after a command | Nothing was changed: every command saves completely or not at all. (The one exception is a wrong owner passphrase, which is recorded in the history.) Read the message; it says what is missing. |
| A reply is rejected | Read the reason (for example a wrong correlation ID means the reply belongs to a different handoff). Ask the assistant to reply again in the format shown, then record it. To keep a reply that cannot be fixed, use `--unstructured`. |
| You recorded the wrong reply | Replies cannot be deleted (they are part of the record). Create a new handoff and record the right reply there. Only the latest reply since the task's current state counts. |
| A task needs changes after it was submitted | `dexter task withdraw TASK-ID`, edit it, then `task submit` again. After approval its content is locked: ask the owner to cancel it and create a new task. |
| An assistant reports it is blocked | `dexter task block TASK-ID --reason "..."`, resolve the problem, then `task unblock`. |
| "needs an interactive terminal" | Owner decisions must be typed by a person in a real terminal. Run the command directly, not through a script or an AI tool. |
| Forgotten owner passphrase | It cannot be recovered. Keep using the old database read-only for reference, and start a new one with `--db new.db` and `init`. |
| You think the file is damaged or was edited | `dexter check` verifies SQLite integrity, the history's hash chain, and that every approved or completed task has a recorded owner decision. |
| Backups | `dexter backup ~/dexter-backup-2026-10-26.db` makes a consistent copy (it never overwrites). To restore, use the copy with `--db`. |

## Working and planned features

**Working now (Phase 3A):**
- Local SQLite storage with projects, tasks, priorities, dependencies,
  acceptance criteria, assignments, approvals, test results, review findings
  and a timestamped, hash-chained activity history.
- Enforced task states and transitions, including dependency, evidence and
  independent-review checks, blocking, cancellation and project pause.
- SIMULATED owner approvals with a local passphrase, terminal-only.
- Manual handoffs in `dexter-handoff/v1` with strict `dexter-response/v1`
  reply checking and flagging of untrusted instructions.
- Command-line interface, progress reports, history, `check` and `backup`.
- Automated tests (`tests/`), run by the existing **Tests** workflow.

**Planned, not built:**
- Real authentication of the owner (for example through GitHub identity), and
  approvals that GitHub itself can check.
- Automatic delivery of handoffs to ChatGPT or Claude Code, with delivery
  confirmation. Needs owner approval of any paid API use first.
- Linking tasks to real branches, pull requests and CI results, so Dexter can
  check test results itself instead of relying on reports.
- Multiple human users and permissions, notifications, and a web interface.
- Connecting to Eden Well. Not permitted in this phase.

## Security limitations

- **Approvals are simulated.** Anyone who knows the passphrase can approve.
  Anyone who can write the database file can change anything in it, including
  approvals. `check` detects casual edits to the history but not a complete
  rewrite. Do not treat Dexter's records as proof of authorisation for
  anything outside Dexter.
- **The terminal-only rule is a speed bump, not a lock.** It stops scripts and
  AI tools that have no terminal. A tool that can drive a real terminal and
  knows the passphrase could still type it. Never share the passphrase with
  an AI assistant.
- **Instruction flagging is a heuristic.** It catches common phrasings, not
  every possible one. The real protection is that Dexter never executes or
  acts on reply text: replies are stored and displayed, nothing more.
- **Reported results are not verified.** Test results and review findings are
  what the assistants said. Check the pull request and CI yourself before
  completing a task.
- **The assistants still act outside Dexter.** The safety rules in each
  handoff ask them to stay in this sandbox and not merge, deploy or spend.
  Dexter cannot enforce what they do in their own tools.

## Demonstration

`examples/coordinator_demo.py` runs a fictional project ("Pocket Library", a
book-lending tool) from request to owner acceptance in a throwaway database:

```sh
python3 examples/coordinator_demo.py
```

It shows an architecture review by ChatGPT, a SIMULATED owner approval, a
dependency stopping a task from starting early, the implementation handoff to
Claude Code, a reply that falsely claims the owner already approved it
(flagged and ignored), the independent review, the owner's acceptance, the
progress report and the history. The demo writes its passphrase into the
script so it can run unattended. The ChatGPT and Claude Code replies are
pre-written; nothing is sent anywhere.
