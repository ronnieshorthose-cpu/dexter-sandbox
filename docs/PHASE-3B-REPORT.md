# Dexter Progress Report: Phase 3B guided interface

**Date:** 2026-10-26
**Repository:** `ronnieshorthose-cpu/dexter-sandbox`
**Branch:** `dexter/phase-3b-guided-interface` (from `main` at `9492aad`, the Phase 3A merge)

## Assignment

Make Dexter usable as a practical coordinator between the human owner, ChatGPT
and Claude Code, without memorising Python commands:

- a beginner-friendly guided interface;
- a local status dashboard, plus an optional read-only HTML page with no
  server, network access or approval buttons;
- easy copying of handoffs and pasting of replies;
- every AI response labelled unverified;
- a Windows setup guide, a `dexter.bat` launcher and a `windows-latest` test
  job;
- a complete walkthrough.

All Phase 3A safeguards had to stay intact. The work had to avoid Eden Well,
paid APIs, secrets, automatic merges and deployments, and changes to
repository settings.

## Current status

**Awaiting human review.**
- **Done:** implemented, tested and documented on the branch above.
- **Tests:** 197 tests pass locally on Linux (Python 3.13): the 119 Phase 3A
  tests unchanged, plus 78 new ones. The 188 AI-reviewer tests also pass.
- **Not yet confirmed:** the Windows job runs for the first time on the pull
  request; see "Problems encountered".
- **Nothing has been merged or deployed.**

## Completed tasks

### The guided menu

`dexter_coordinator/guided.py`, opened with `python -m dexter_coordinator start`
or by double-clicking `dexter.bat`.

- **Home screen:** shows what is waiting for the owner, what is waiting for
  each AI, reviews, active tasks and blocked tasks.
- **"Do the next step":** lists open tasks, most urgent first. For each task it
  offers the steps that make sense now, with the recommended one first.
- **Wizards:**
  - first-time setup;
  - creating projects and tasks (with acceptance criteria, priority and
    dependencies);
  - preparing and copying handoffs, and pasting replies;
  - owner decisions, reports, history, checks, backups and changing the
    passphrase.
- **Same engine:** every step calls the same `Coordinator` method as the
  command line. The menu adds no rule and no shortcut.

### Owner actions in the menu

- **Banner:** the same SIMULATED-approval banner as Phase 3A.
- **Passphrase:** asked once per action through the Phase 3A
  interactive-terminal prompt, and never stored.
- **Cancelling:** an empty passphrase cancels. It is no longer recorded as a
  wrong attempt.
- **Final acceptance:** first asks whether the owner has checked the pull
  request and its test results on GitHub; "no" stops without changes.

### Handoff exchange

`dexter_coordinator/exchange.py`:

- **Folders:** private folders next to the database (`~/.dexter/exchange/`
  and `~/.dexter/backups/`), never inside a git working tree, never through
  symbolic links. Files are never overwritten, except the dashboard page,
  which is rebuilt each time.
- **Sending:** handoffs are saved to an outbox and then:
  - copied to the clipboard on a best-effort basis (`tkinter`, Windows and
    macOS);
  - or opened in Notepad;
  - or shown on screen.
- **Receiving:** replies are pasted into a Notepad file (byte-order marks and
  Windows line endings handled) or straight into the window. The file is
  deleted once recorded.

### Labels

`dexter_coordinator/labels.py`. Every AI reply, on every screen, starts with
**UNVERIFIED AI RESPONSE**, and its status line has three stages:
1. "Nobody has independently checked it."
2. "Independently reviewed by ChatGPT, whose review is also an unverified AI
   opinion."
3. After acceptance: "The owner accepted this task in Dexter (SIMULATED
   approval), but the reply text is still only what the AI claimed."

No label ever calls AI output verified. The command line's `handoff show` and
`handoff list` use the same labels.

### Dashboard

`dexter_coordinator/dashboard.py`. The text dashboard is the menu's home
screen, also available as `python -m dexter_coordinator dashboard`. The
optional HTML page (`dashboard --html`, or `[D]` in the menu) is a static file:
- no server, scripts, forms or buttons;
- every value HTML-escaped;
- a Content-Security-Policy that blocks loading anything.

### Windows

- **`dexter.bat`** (stored with Windows line endings; see `.gitattributes`):
  - finds Python 3.9 or newer (the `py` launcher first, then `python`);
  - checks that it is inside the Dexter folder and that `hashlib.scrypt` is
    available;
  - explains how to install Python if it is missing;
  - reports launch failures in plain English and waits for a key so the
    message can be read.
- **`windows-latest` job:** added to the existing unprivileged Tests workflow.
  It runs the application tests and a smoke test of `dexter.bat --version`.

### Engine change

One read-only helper, `Coordinator.handoff_is_current`. It lets the menu tell
current handoffs from out-of-date ones. There are no schema, state,
transition, signature or authentication changes, and the database version is
still 2.

### Documentation

- **[GETTING-STARTED-WINDOWS.md](GETTING-STARTED-WINDOWS.md):** setup and
  troubleshooting.
- **[WALKTHROUGH.md](WALKTHROUGH.md):** one project from plan to final
  approval. Its menu lines are checked against the real output by a test.
- **Updated:** the README, [COORDINATOR.md](COORDINATOR.md) and
  [COORDINATOR-SECURITY.md](COORDINATOR-SECURITY.md).

## Test results

Run locally (Linux, Python 3.13):

| Suite | Result |
|---|---|
| `python3 -m unittest discover -s tests -t . -v` | 197 passed (119 Phase 3A + 78 Phase 3B), 0 failed, 0 skipped |
| `python3 -m unittest discover -s .github/ai_review/reviewer_tests -t .github/ai_review -v` | 188 passed |

The new tests are in `tests/test_guided_workflow.py` (30) and
`tests/test_guided_safety.py` (48). They cover:

- **Workflow:**
  - the full walkthrough from an empty database to owner acceptance, with
    signature checks passing;
  - every menu branch: first run, task creation with dependencies, approval,
    implementation, review, changes requested, blocked replies, block and
    unblock, pause and resume, several projects, reports, checks, backups and
    passphrase change;
  - invalid input, end of input and Ctrl+C.
- **Passphrase:**
  - asked once per owner action and never kept by the menu;
  - never written to any file, including the database, outbox, reports,
    dashboard and backups;
  - an empty passphrase cancels without counting as a wrong attempt; a wrong
    one is refused and recorded;
  - piped input is still refused;
  - the real `start` command was driven through a pseudo-terminal.
- **Labels:** every screen that shows a reply labels it UNVERIFIED, and no
  label says "verified". Owner screens say SIMULATED and claim nothing on
  GitHub. Replies that claim approval are flagged, and replies with an
  `approved` field are refused.
- **Engine rules:**
  - review needs a `done` report;
  - "ready" needs an independent review, which goes to the other AI;
  - owner decisions are refused before the passphrase is asked when the rules
    forbid them;
  - the schema is unchanged.
- **Files:** private modes, git working trees and symbolic links refused, no
  overwriting, Notepad encodings, reply files removed once recorded.
- **HTML page:** no scripts, forms, buttons or external URLs; untrusted text
  escaped; owner secrets never shown; the browser is given a local `file:` URL
  only.
- **Clipboard:** unavailable on Linux, missing `tkinter` and Tk errors are
  reported rather than raised, and text is sanitised.
- **Launcher:** CRLF line endings, its checks and messages, and no downloads,
  installs, git, PowerShell, deletes or registry edits.
- **Isolation:** no server, socket, subprocess or network code in the package;
  browser and clipboard code only in `exchange.py`; the new modules never read
  the owner record.

## Problems encountered

- **Windows found a Phase 3A gap, now fixed.** The first `windows-latest` run
  failed one Phase 3A test, `test_backup_refuses_a_dangling_symlink`. Windows
  has no `O_NOFOLLOW`, so creating a file at a dangling symbolic link followed
  the link and created its target.
  - **Fix:** `store._create_private_file` and the new `exchange._write_new` now
    refuse any path that already exists, including a dangling link, before
    opening it.
  - **Regression test:** added for the exchange files.
  - **Remaining limit:** a small race is left on Windows, between the check and
    the creation. Creating symbolic links there normally needs administrator
    rights or Developer Mode.
- **Windows not yet run.** The code was written and tested on Linux. The new
  `windows-latest` job will be the first real Windows run, including the Phase
  3A tests on Windows.
- **Not testable in CI:**
  - the clipboard and Notepad (tests replace them with stand-ins);
  - a double-click start of `dexter.bat` (CI checks it with `--version` only);
  - the real terminal path on Windows (the pseudo-terminal test is
    POSIX-only).

  These need a manual check on the owner's laptop; the walkthrough is the
  checklist.
- **macOS:** not tested; the clipboard path assumes Tk behaves there as
  documented.

## Security limitations (unchanged from Phase 3A unless stated)

**Approvals:**
- Approvals are SIMULATED. Whoever knows the passphrase is the owner as far as
  Dexter can tell.
- Someone who can read the database can guess passphrases offline.
- Someone who can write it can delete or roll it back.
- See [COORDINATOR-SECURITY.md](COORDINATOR-SECURITY.md).

**The menu and the terminal:**
- The terminal check is not authentication. A program that controls the
  owner's terminal window could type into the prompt.
- Keep AI tools off the owner's Windows account; Claude Code on the web cannot
  reach the laptop.

**Exchange files (new):** they are as private as the owner's account. A program
running as the owner could change a pasted reply file before it is recorded.
Dexter shows the parsed reply before the owner confirms.

**Clipboard (new):** Windows clipboard history may keep handoff text. It
contains no secrets.

**Windows permissions (new):** `check` warns about POSIX permissions only.
Dexter relies on the Windows user folder being private, which is the default.

**AI replies:** reply authorship and reported test results are not verified.
The owner checks the pull request and CI on GitHub before accepting.

## Estimated additional costs

None. No paid services, API calls, keys or secrets were added. The Windows
test job runs on GitHub's standard hosted runners under the existing Tests
workflow.

## Pending human decisions

- Review and test the pull request, ideally by following
  [WALKTHROUGH.md](WALKTHROUGH.md) on the Windows laptop.
- Decide whether to merge. Nothing is merged without the owner's explicit
  approval.

## Next recommended action

Check the `windows-latest` job on the pull request. Then try `dexter.bat` on
the Windows laptop with a throwaway project, and confirm that the clipboard,
Notepad and passphrase prompts behave as the walkthrough describes.
