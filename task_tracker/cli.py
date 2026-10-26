"""Command-line interface for the task tracker.

Usage:
    python -m task_tracker add "Title" [-d "Description"]
    python -m task_tracker list
    python -m task_tracker complete ID

Use --file PATH to choose the JSON file (default: tasks.json in the current
directory, or the TASK_TRACKER_FILE environment variable if set).
"""

import argparse
import os
import sys

from .tracker import StorageError, TaskError, load_tasks, save_tasks

DEFAULT_FILE = "tasks.json"


class _Parser(argparse.ArgumentParser):
    """ArgumentParser that raises instead of exiting, so main() controls exit codes."""

    def error(self, message):
        raise TaskError(message)


def build_parser():
    parser = _Parser(prog="task_tracker", description="A simple task tracker.")
    parser.add_argument(
        "--file",
        default=os.environ.get("TASK_TRACKER_FILE", DEFAULT_FILE),
        help="path to the JSON task file (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="command", parser_class=_Parser)

    add = sub.add_parser("add", help="add a task")
    add.add_argument("title", help="task title")
    add.add_argument("-d", "--description", default="", help="task description")

    sub.add_parser("list", help="list all tasks")

    complete = sub.add_parser("complete", help="mark a task as completed")
    complete.add_argument("id", help="ID of the task to complete")
    return parser


def format_task(task):
    mark = "x" if task.completed else " "
    line = f"[{mark}] {task.id}. {task.title}"
    if task.description:
        line += f" - {task.description}"
    return line


def main(argv=None, out=None, err=None):
    out = out or sys.stdout
    err = err or sys.stderr
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command is None:
            parser.print_help(out)
            return 1

        tracker = load_tasks(args.file)

        if args.command == "add":
            task = tracker.add(args.title, args.description)
            save_tasks(args.file, tracker)
            print(f"Added task {task.id}: {task.title}", file=out)
        elif args.command == "list":
            tasks = tracker.list()
            if not tasks:
                print("No tasks yet.", file=out)
            for task in tasks:
                print(format_task(task), file=out)
        elif args.command == "complete":
            task = tracker.complete(args.id)
            save_tasks(args.file, tracker)
            print(f"Completed task {task.id}: {task.title}", file=out)
        return 0
    except TaskError as exc:
        print(f"Error: {exc}", file=err)
        return 2
    except StorageError as exc:
        print(f"Error: {exc}", file=err)
        print("The task file was not changed.", file=err)
        return 3
