import io
import json
import os
import tempfile
import unittest

from task_tracker.cli import main
from task_tracker.tracker import (
    StorageError,
    TaskError,
    TaskTracker,
    load_tasks,
    save_tasks,
)


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "tasks.json")

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = main(["--file", self.path, *args], out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def write_raw(self, content):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(content)

    def read_raw(self):
        with open(self.path, "r", encoding="utf-8") as f:
            return f.read()


class AddTaskTests(TempDirTestCase):
    def test_add_assigns_sequential_ids(self):
        tracker = TaskTracker()
        first = tracker.add("Buy milk", "2 litres")
        second = tracker.add("Walk dog")
        self.assertEqual((first.id, second.id), (1, 2))
        self.assertEqual(first.description, "2 litres")
        self.assertFalse(first.completed)

    def test_add_strips_whitespace(self):
        task = TaskTracker().add("  Title  ", "  Desc  ")
        self.assertEqual((task.title, task.description), ("Title", "Desc"))

    def test_add_via_cli_persists(self):
        code, out, _ = self.run_cli("add", "Write report", "-d", "Quarterly")
        self.assertEqual(code, 0)
        self.assertIn("Added task 1", out)
        data = json.loads(self.read_raw())
        self.assertEqual(data["tasks"][0]["title"], "Write report")
        self.assertEqual(data["tasks"][0]["description"], "Quarterly")

    def test_ids_not_reused_after_reload(self):
        self.run_cli("add", "One")
        self.run_cli("add", "Two")
        tracker = load_tasks(self.path)
        self.assertEqual(tracker.add("Three").id, 3)


class ListTaskTests(TempDirTestCase):
    def test_list_empty(self):
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("No tasks yet.", out)
        self.assertFalse(os.path.exists(self.path), "list must not create a file")

    def test_list_shows_all_tasks_and_status(self):
        self.run_cli("add", "Alpha", "-d", "first")
        self.run_cli("add", "Beta")
        self.run_cli("complete", "2")
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertEqual(
            out.splitlines(), ["[ ] 1. Alpha - first", "[x] 2. Beta"]
        )

    def test_list_returns_copy(self):
        tracker = TaskTracker()
        tracker.add("Alpha")
        tracker.list().clear()
        self.assertEqual(len(tracker.list()), 1)


class CompleteTaskTests(TempDirTestCase):
    def test_complete_marks_task(self):
        tracker = TaskTracker()
        tracker.add("Alpha")
        task = tracker.complete(1)
        self.assertTrue(task.completed)

    def test_complete_accepts_string_id(self):
        tracker = TaskTracker()
        tracker.add("Alpha")
        self.assertTrue(tracker.complete(" 1 ").completed)

    def test_complete_is_idempotent(self):
        tracker = TaskTracker()
        tracker.add("Alpha")
        tracker.complete(1)
        self.assertTrue(tracker.complete(1).completed)

    def test_complete_via_cli_persists(self):
        self.run_cli("add", "Alpha")
        code, out, _ = self.run_cli("complete", "1")
        self.assertEqual(code, 0)
        self.assertIn("Completed task 1", out)
        self.assertTrue(load_tasks(self.path).list()[0].completed)


class SaveReloadTests(TempDirTestCase):
    def test_round_trip(self):
        tracker = TaskTracker()
        tracker.add("Alpha", "first")
        tracker.add("Beta", "second")
        tracker.complete(2)
        save_tasks(self.path, tracker)

        reloaded = load_tasks(self.path)
        self.assertEqual(reloaded.list(), tracker.list())

    def test_round_trip_unicode(self):
        tracker = TaskTracker()
        tracker.add("Café ☕", "naïve – résumé")
        save_tasks(self.path, tracker)
        self.assertEqual(load_tasks(self.path).list()[0].title, "Café ☕")

    def test_missing_file_loads_empty(self):
        self.assertEqual(load_tasks(self.path).list(), [])

    def test_save_leaves_no_temp_files(self):
        save_tasks(self.path, TaskTracker())
        self.assertEqual(os.listdir(self._tmp.name), ["tasks.json"])

    def test_save_to_missing_directory_raises_storage_error(self):
        bad_path = os.path.join(self._tmp.name, "no", "such", "dir", "tasks.json")
        with self.assertRaises(StorageError):
            save_tasks(bad_path, TaskTracker())


class InvalidInputTests(TempDirTestCase):
    def test_empty_title_rejected(self):
        with self.assertRaises(TaskError):
            TaskTracker().add("   ")

    def test_overlong_title_rejected(self):
        with self.assertRaises(TaskError):
            TaskTracker().add("x" * 201)

    def test_overlong_description_rejected(self):
        with self.assertRaises(TaskError):
            TaskTracker().add("ok", "x" * 2001)

    def test_complete_unknown_id(self):
        tracker = TaskTracker()
        tracker.add("Alpha")
        with self.assertRaises(TaskError):
            tracker.complete(99)

    def test_complete_bad_ids(self):
        tracker = TaskTracker()
        tracker.add("Alpha")
        for bad in ["abc", "", "1.5", "0", "-1", True, None]:
            with self.subTest(bad=bad), self.assertRaises(TaskError):
                tracker.complete(bad)

    def test_cli_invalid_inputs_do_not_crash(self):
        self.run_cli("add", "Alpha")
        before = self.read_raw()
        cases = [
            ("add", ""),
            ("complete", "abc"),
            ("complete", "42"),
            ("complete",),
            ("unknown-command",),
            ("add",),
        ]
        for args in cases:
            with self.subTest(args=args):
                code, out, err = self.run_cli(*args)
                self.assertEqual(code, 2)
                self.assertTrue(err.startswith("Error:"))
                self.assertNotIn("Traceback", err)
        self.assertEqual(self.read_raw(), before, "invalid input must not change data")

    def test_cli_no_command_shows_help(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("usage:", out)


class CorruptedFileTests(TempDirTestCase):
    CORRUPT_CASES = {
        "truncated json": '{"tasks": [{"id": 1, "title": "A"',
        "not json": "this is not json",
        "empty file": "",
        "wrong top-level type": "[1, 2, 3]",
        "tasks not a list": '{"tasks": "nope"}',
        "task not an object": '{"tasks": ["nope"]}',
        "missing title": '{"tasks": [{"id": 1}]}',
        "bad id type": '{"tasks": [{"id": "1", "title": "A"}]}',
        "bool id": '{"tasks": [{"id": true, "title": "A"}]}',
        "bad completed type": '{"tasks": [{"id": 1, "title": "A", "completed": "yes"}]}',
        "duplicate ids": '{"tasks": [{"id": 1, "title": "A"}, {"id": 1, "title": "B"}]}',
    }

    def test_load_raises_storage_error(self):
        for name, content in self.CORRUPT_CASES.items():
            with self.subTest(case=name):
                self.write_raw(content)
                with self.assertRaises(StorageError):
                    load_tasks(self.path)

    def test_invalid_utf8_raises_storage_error(self):
        with open(self.path, "wb") as f:
            f.write(b"\xff\xfe\x00garbage")
        with self.assertRaises(StorageError):
            load_tasks(self.path)

    def test_cli_reports_error_and_preserves_corrupt_file(self):
        for name, content in self.CORRUPT_CASES.items():
            for args in (("list",), ("add", "New"), ("complete", "1")):
                with self.subTest(case=name, args=args):
                    self.write_raw(content)
                    code, _, err = self.run_cli(*args)
                    self.assertEqual(code, 3)
                    self.assertIn("Error:", err)
                    self.assertIn("not changed", err)
                    self.assertNotIn("Traceback", err)
                    self.assertEqual(self.read_raw(), content)


if __name__ == "__main__":
    unittest.main()
