# Phase 2 Implementation Plan — Simple Task Tracker

**Date:** 2026-10-26
**Repository:** `ronnieshorthose-cpu/dexter-sandbox`
**Branch:** `dexter/phase-2-coordination-test`

## Scope, risk, and cost check (Workflow step 2)

- Repository is authorized for this assignment. No other repository is touched.
- Python standard library only; nothing is installed. No external services, APIs, or paid resources.
- No existing files are modified or deleted. No settings, branch protection, merge, or deployment.
- Estimated additional cost: none.

## Files to create

| File | Purpose |
|------|---------|
| `task_tracker/__init__.py` | Package marker |
| `task_tracker/__main__.py` | Lets the app run as `python3 -m task_tracker` |
| `task_tracker/tracker.py` | Task model, in-memory tracker, input validation, JSON load/save |
| `task_tracker/cli.py` | Command-line interface (`add`, `list`, `complete`) and error handling |
| `task_tracker/README.md` | Usage instructions |
| `tests/__init__.py`, `tests/test_tracker.py` | Automated tests (`unittest`) |
| `.gitignore` | Keeps local `tasks.json` and Python caches out of git |
| `docs/PHASE-2-PLAN.md` | This plan |
| `docs/PHASE-2-REPORT.md` | Completion report (from `DEXTER-REPORT-TEMPLATE.md`) |

## Application features

1. `add TITLE [-d DESCRIPTION]` — adds a task with the next free ID.
2. `list` — shows every task with `[ ]` / `[x]` completion status.
3. `complete ID` — marks a task as completed.
4. Saves to a local JSON file after every change, using an atomic write (temp file + rename) so a crash mid-save cannot corrupt existing data.
5. Reloads tasks from the JSON file on every run; a missing file means "no tasks yet".
6. Invalid input (empty/overlong title, non-numeric or unknown ID, unknown command) prints a clear error and exits with a non-zero code — no traceback.
7. A corrupted or malformed JSON file is reported and **left untouched** (never overwritten) so data can be recovered.

## Tests to perform

- Adding tasks (IDs, whitespace handling, persistence via CLI, IDs not reused after reload)
- Listing tasks (empty list, mixed status output, list doesn't create files)
- Completing tasks (by int or string ID, idempotent, persisted)
- Saving and reloading (round trip, Unicode, missing file, no leftover temp files, unwritable path)
- Invalid input (library-level and CLI-level; data unchanged after errors)
- Corrupted JSON (truncated, non-JSON, empty, wrong shapes, bad field types, duplicate IDs, invalid UTF-8; CLI preserves file)
- Plus a manual end-to-end smoke test of the CLI.

## Risks and limitations

- No file locking: two copies running at the same moment could lose one update.
- Whole file is read and rewritten each run; fine for personal use, not for very large lists.
- No delete/edit/undo commands (not requested).
- Review is self-review only; independent AI review is not configured (see `DEXTER-WORKFLOW.md`).
