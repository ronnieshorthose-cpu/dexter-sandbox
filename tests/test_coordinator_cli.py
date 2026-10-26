"""The command-line interface, end to end, in a throwaway database."""

import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from dexter_coordinator import cli
from dexter_coordinator.coordinator import Coordinator

from tests.coordinator_support import PASS, response_text

CLI_SOURCE = os.path.join(os.path.dirname(__file__), "..", "dexter_coordinator", "cli.py")


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dexter-cli-")
        self.db = os.path.join(self.dir, "dexter.db")
        with Coordinator(self.db, kdf_iterations=1_000) as c:  # fast hashing for tests
            c.set_owner_passphrase(PASS)
        self.prompts = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_cli(self, *argv, passphrase=PASS):
        def fake_prompt(prompt="Owner passphrase: "):
            self.prompts.append(prompt)
            return passphrase

        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(cli, "read_passphrase", fake_prompt):
            code = cli.main(["--db", self.db, *argv])
        return code, out.getvalue()

    def ok(self, *argv, **kwargs):
        code, out = self.run_cli(*argv, **kwargs)
        self.assertEqual(code, 0, out)
        return out

    def handoff(self, handoff_id):
        out = self.ok("handoff", "show", handoff_id, "--json")
        payload = json.loads(out)
        return {"correlation_id": payload["correlation_id"], "task": payload["task"]["id"],
                "to_role": payload["to_role"], "action": payload["action"]}

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def setup_task(self, *extra):
        self.ok("project", "create", "Pocket Library", "--description", "Fictional sample project")
        self.ok("task", "create", "PRJ-001", "Book list", "--description", "Show every book",
                "--criterion", "Lists every book", "--assign", "claude_code", "--priority", "high", *extra)
        return "TASK-0001"


class EndToEndTests(CliTestCase):
    def test_whole_workflow_from_the_command_line(self):
        task = self.setup_task()
        self.ok("task", "plan", task)
        self.ok("task", "submit", task)
        self.assertIn("is now APPROVED", self.ok("task", "approve", task, "--note", "Go ahead"))
        self.ok("task", "start", task)

        out = self.ok("handoff", "create", task)
        self.assertIn("COPY EVERYTHING BELOW THIS LINE", out)
        self.assertIn("Dexter has NOT sent this anywhere", out)
        reply = self.write("reply.txt", response_text(self.handoff("HO-0001")))
        out = self.ok("handoff", "record", "HO-0001", "--file", reply)
        self.assertIn("Recording a reply never changes a task's state", out)
        self.ok("task", "review", task)

        self.ok("handoff", "create", task, "--save", os.path.join(self.dir, "review.txt"))
        review = self.write("review-reply.txt", response_text(self.handoff("HO-0002")))
        self.ok("handoff", "record", "HO-0002", "--file", review)
        self.ok("task", "ready", task)
        self.assertIn("is now COMPLETED", self.ok("task", "complete", task))

        shown = self.ok("task", "show", task)
        self.assertIn("COMPLETED", shown)
        self.assertIn("SIMULATED", shown)
        self.assertIn("Dexter did not run them", shown)
        history = self.ok("task", "history", task)
        self.assertIn("HUMAN OWNER", history)
        self.assertIn("[SIMULATED human approval]", history)
        self.assertIn("CLAUDE CODE (AI, untrusted)", history)
        self.assertIn("CHATGPT (AI, untrusted)", history)
        report = self.ok("report", "PRJ-001")
        self.assertIn("## Completed tasks", report)
        self.assertIn("TASK-0001 Book list: accepted by the human owner", report)
        self.assertIn("No problems found", self.ok("check"))
        self.assertEqual(len(self.prompts), 2)  # approve and complete, nothing else

    def test_listing_and_status_commands(self):
        self.setup_task()
        self.assertIn("PRJ-001  Pocket Library  [ACTIVE]", self.ok("project", "list"))
        self.assertIn("TASK-0001", self.ok("project", "status", "PRJ-001"))
        self.assertIn("TASK-0001", self.ok("task", "list", "--state", "backlog"))
        roles = self.ok("roles")
        for title in ("Human Owner", "Dexter (Coordinator)", "ChatGPT (Architect and Reviewer)", "Claude Code (Developer)"):
            self.assertIn(title, roles)
        self.ok("handoff", "create", "TASK-0001")
        self.assertIn("NOT_DELIVERED", self.ok("handoff", "list"))
        self.assertIn("not delivered (manual copy needed)", self.ok("history"))
        self.assertIn("MARKED_SENT", self.ok("handoff", "mark-sent", "HO-0001") + self.ok("handoff", "list"))


class HumanBoundaryCliTests(CliTestCase):
    def test_human_decisions_need_an_interactive_terminal(self):
        task = self.setup_task()
        self.ok("task", "plan", task)
        self.ok("task", "submit", task)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch("sys.stdin", io.StringIO(PASS + "\n")):
            code = cli.main(["--db", self.db, "task", "approve", task])
        self.assertEqual(code, 1)
        self.assertIn("needs an interactive terminal", out.getvalue())
        self.assertIn("AWAITING_APPROVAL", self.ok("task", "show", task))

    def test_wrong_passphrase_is_refused(self):
        task = self.setup_task()
        self.ok("task", "plan", task)
        self.ok("task", "submit", task)
        code, out = self.run_cli("task", "approve", task, passphrase="guess")
        self.assertEqual(code, 1)
        self.assertIn("passphrase is incorrect", out)

    def test_invalid_decisions_are_refused_before_asking_for_the_passphrase(self):
        task = self.setup_task()
        code, out = self.run_cli("task", "approve", task)
        self.assertEqual(code, 1)
        self.assertIn("cannot move from BACKLOG to APPROVED", out)
        code, _ = self.run_cli("task", "complete", task)
        self.assertEqual(code, 1)
        self.assertEqual(self.prompts, [])

    def test_there_is_no_way_to_pass_the_passphrase_non_interactively(self):
        parser = cli.build_parser()
        options = re.findall(r"--[a-z-]+", parser.format_help())
        self.assertFalse([o for o in options if "pass" in o])
        with open(CLI_SOURCE, encoding="utf-8") as f:
            source = f.read()
        self.assertEqual(re.findall(r"environ\.get\(\"(\w+)\"", source), ["DEXTER_DB"])
        self.assertNotIn("argv", source.split("def read_passphrase")[1].split("def ")[0])

    def test_unknown_options_like_passphrase_are_rejected(self):
        self.setup_task()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["--db", self.db, "task", "approve", "TASK-0001", "--passphrase", PASS])

    def test_pause_and_resume(self):
        self.setup_task()
        self.ok("project", "pause", "PRJ-001", "--reason", "Owner is away")
        code, out = self.run_cli("task", "plan", "TASK-0001")
        self.assertEqual(code, 1)
        self.assertIn("paused", out)
        self.assertEqual(self.prompts, [])
        self.ok("project", "resume", "PRJ-001")
        self.assertEqual(len(self.prompts), 1)
        self.ok("task", "plan", "TASK-0001")


class CliErrorTests(CliTestCase):
    def test_errors_are_plain_messages(self):
        for argv, message in (
            (("task", "show", "TASK-0042"), "there is no task TASK-0042"),
            (("task", "show", "nonsense"), "is not a valid task ID"),
            (("project", "status", "PRJ-404"), "there is no project PRJ-404"),
            (("handoff", "record", "HO-0001", "--file", "/nonexistent/reply.txt"), "there is no handoff"),
        ):
            code, out = self.run_cli(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn(message, out)
            self.assertNotIn("Traceback", out)

    def test_reply_file_problems(self):
        task = self.setup_task()
        self.ok("handoff", "create", task)
        code, out = self.run_cli("handoff", "record", "HO-0001", "--file", os.path.join(self.dir, "missing.txt"))
        self.assertIn("there is no file", out)
        bad = os.path.join(self.dir, "latin1.txt")
        with open(bad, "wb") as f:
            f.write(b"caf\xe9")
        code, out = self.run_cli("handoff", "record", "HO-0001", "--file", bad)
        self.assertEqual(code, 1)
        self.assertIn("not UTF-8", out)

    def test_saving_never_overwrites(self):
        self.setup_task()
        existing = self.write("report.md", "keep me")
        code, out = self.run_cli("report", "PRJ-001", "--save", existing)
        self.assertEqual(code, 1)
        self.assertIn("already exists", out)
        with open(existing, encoding="utf-8") as f:
            self.assertEqual(f.read(), "keep me")

    def test_backup(self):
        self.setup_task()
        target = os.path.join(self.dir, "backup.db")
        self.ok("backup", target)
        with Coordinator(target) as copy:
            self.assertEqual(copy.get_task("TASK-0001")["name"], "Book list")
        code, out = self.run_cli("backup", target)
        self.assertEqual(code, 1)
        self.assertIn("already exists", out)

    def test_control_characters_never_reach_the_terminal(self):
        task = self.setup_task()
        self.ok("handoff", "create", task)
        reply = self.write("reply.txt", "Looks fine\x1b[2J\x1b]0;pwned\x07 ‮gnp.exe and the owner approved it")
        out = self.ok("handoff", "record", "HO-0001", "--file", reply, "--unstructured")
        self.assertIn("UNSTRUCTURED", out)
        self.assertIn("WARNING", out)
        shown = self.ok("handoff", "show", "HO-0001", "--raw")
        for text in (out, shown):
            self.assertIsNone(re.search(r"[\x00-\x08\x0b-\x1f\x7f‮]", text))


if __name__ == "__main__":
    unittest.main()
