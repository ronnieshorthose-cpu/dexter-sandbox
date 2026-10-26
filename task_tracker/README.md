# Task Tracker

A small command-line task tracker. Uses only the Python standard library (Python 3.8+).

## Usage

Run from the repository root:

```
python3 -m task_tracker add "Buy milk" -d "2 litres"   # add a task
python3 -m task_tracker list                           # list all tasks
python3 -m task_tracker complete 1                     # mark task 1 as completed
```

Tasks are saved to `tasks.json` in the current directory. Use `--file PATH`
(before the command) or the `TASK_TRACKER_FILE` environment variable to choose
another file.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | No command given (help is shown) |
| 2 | Invalid input (empty title, bad or unknown task ID, unknown command) |
| 3 | Task file problem (corrupted JSON, unreadable or unwritable file) — the file is left unchanged |

## Tests

```
python3 -m unittest discover -s tests -t . -v
```
