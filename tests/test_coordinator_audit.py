"""Regression tests for the Phase 3A security audit (findings H-1 to L-4).

Each class reproduces one finding from docs/PHASE-3A-SECURITY-ASSESSMENT.md. They
were written and run against the audited commit f4cc8a1 first (where they fail),
then against the fix.
"""

import contextlib
import hashlib
import inspect
import io
import json
import os
import select
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

from dexter_coordinator import cli
from dexter_coordinator import handoff as ho
from dexter_coordinator.auth import AuthError
from dexter_coordinator.coordinator import Coordinator
from dexter_coordinator.models import DexterError, RuleError, ValidationError
from dexter_coordinator.store import StoreError, dumps, event_hash

from tests.coordinator_support import PASS, CoordinatorTestCase, make_coordinator, response_text

ROOT = os.path.join(os.path.dirname(__file__), "..")


def record(c, handoff_id, text, passphrase=PASS, **kwargs):
    """Record a reply as the owner (works with both the audited and the fixed API)."""
    if "passphrase" in inspect.signature(c.record_response).parameters:
        return c.record_response(handoff_id, text, passphrase, **kwargs)
    return c.record_response(handoff_id, text, **kwargs)


def sql(path, statement, params=()):
    conn = sqlite3.connect(path)
    try:
        result = conn.execute(statement, params).fetchall()
        conn.commit()
        return result
    finally:
        conn.close()


class H1OwnerMustRecordReplies(CoordinatorTestCase):
    """H-1: a local process without the passphrase could record replies and fake the review."""

    def test_recording_a_reply_needs_the_owner_passphrase(self):
        task = self.in_progress()
        h = self.c.create_handoff(task)
        for bad in (None, "", "wrong-passphrase"):
            with self.subTest(passphrase=bad), self.assertRaises(AuthError):
                self.c.record_response(h["id"], response_text(h), bad)
        self.assertIsNone(self.c.get_handoff(h["id"])["response"])

    def test_marking_sent_needs_the_owner_passphrase(self):
        h = self.c.create_handoff(self.new_task())
        with self.assertRaises(AuthError):
            self.c.mark_handoff_sent(h["id"])
        self.assertEqual(self.c.get_handoff(h["id"])["delivery"], "NOT_DELIVERED")

    def test_agent_cannot_reach_ready_for_human_without_the_passphrase(self):
        task = self.in_progress()
        h = self.c.create_handoff(task)
        with self.assertRaises(AuthError):
            self.c.record_response(h["id"], response_text(h))
        with self.assertRaises(RuleError):
            self.c.send_to_review(task)
        self.assertEqual(self.state(task), "IN_PROGRESS")

    def test_sender_identity_is_shown_as_a_claim(self):
        task = self.in_review()
        self.review(task)
        record_ = self.c.get_task(task)
        self.assertFalse(record_.get("reviewer_verified", True))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.show_task(record_)
        self.assertIn("claims to be from ChatGPT", out.getvalue())
        self.assertNotIn("Latest review (ChatGPT", out.getvalue())


class H2ApprovalIntegrity(CoordinatorTestCase):
    """H-2: approvals in the task table were trusted without checking the history."""

    def forge_approval(self, task, *, with_history=False):
        content = {"name": "Book list", "description": "Show every book in the library.", "priority": "medium",
                   "assignee": "claude_code", "criteria": ["Lists every book"], "depends_on": []}
        digest = hashlib.sha256(dumps(content).encode()).hexdigest()
        approval = {"status": "approved", "at": "2026-10-26T00:00:00Z", "label": "SIMULATED", "note": "",
                    "content_hash": digest, "event_seq": 999}
        sql(self.path, "UPDATE tasks SET state='APPROVED', approval=? WHERE key=?", (dumps(approval), task))
        if with_history:
            # Rewrite the chain consistently, as an attacker without the owner's key would.
            last = sql(self.path, "SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1")[0]
            seq, prev = last[0] + 1, last[1]
            details = dumps({"from": "AWAITING_APPROVAL", "to": "APPROVED", "label": "SIMULATED", "content_hash": digest})
            columns = [r[1] for r in sql(self.path, "PRAGMA table_info(events)")]
            if "owner_mac" in columns:
                mac = "0" * 64
                digest_ = event_hash(prev, seq, "2026-10-26T00:00:01Z", 1, 1, "human_owner", "state_changed",
                                     details, mac)
                sql(self.path, "INSERT INTO events (seq, ts, project_id, task_id, actor, action, details, prev_hash, "
                    "hash, owner_mac) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (seq, "2026-10-26T00:00:01Z", 1, 1, "human_owner", "state_changed", details, prev, digest_, mac))
            else:
                digest_ = event_hash(prev, seq, "2026-10-26T00:00:01Z", 1, 1, "human_owner", "state_changed", details)
                sql(self.path, "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)",
                    (seq, "2026-10-26T00:00:01Z", 1, 1, "human_owner", "state_changed", details, prev, digest_))
            approval["event_seq"] = seq
            sql(self.path, "UPDATE tasks SET approval=?, state_seq=? WHERE key=?", (dumps(approval), seq, task))

    def test_forged_approval_field_is_detected_and_refused(self):
        task = self.advance(self.new_task(), "AWAITING_APPROVAL")
        self.forge_approval(task)
        self.assertTrue(self.c.verify())
        with self.assertRaises(DexterError):
            self.c.start(task)

    def test_forged_history_without_the_owner_key_is_detected(self):
        task = self.advance(self.new_task(), "AWAITING_APPROVAL")
        self.forge_approval(task, with_history=True)
        problems = self.c.verify(passphrase=PASS)
        self.assertTrue(any("signature" in p for p in problems), problems)
        with self.assertRaises(DexterError):
            self.c.cancel(task, PASS, "owner decisions refuse to run on tampered records")

    def test_deleting_the_newest_history_is_detected(self):
        task = self.in_progress()
        sql(self.path, "DELETE FROM events WHERE seq = (SELECT max(seq) FROM events)")
        self.assertTrue(self.c.verify())
        self.assertEqual(self.state(task), "IN_PROGRESS")

    def test_state_that_disagrees_with_history_is_detected(self):
        task = self.in_progress()
        sql(self.path, "UPDATE tasks SET state='IN_REVIEW' WHERE key=?", (task,))
        self.assertTrue(any("history" in p for p in self.c.verify()))

    def test_edited_reply_is_detected(self):
        task = self.in_progress()
        h = self.report_done(task)
        sql(self.path, "UPDATE responses SET outcome='blocked' WHERE handoff_id=1")
        self.assertTrue(self.c.verify())
        del h

    def test_owner_decisions_are_signed(self):
        self.approved()
        columns = [r[1] for r in sql(self.path, "PRAGMA table_info(events)")]
        self.assertIn("owner_mac", columns)
        rows = sql(self.path, "SELECT actor, owner_mac FROM events")
        for actor, mac in rows:
            self.assertEqual(actor == "human_owner", mac is not None)


class M1StaleReplies(CoordinatorTestCase):
    """M-1: a reply to a handoff issued before the task changed still counted."""

    def test_reply_to_a_review_handoff_from_before_the_rework_is_rejected(self):
        task = self.in_review()
        old_review = self.c.create_handoff(task)
        self.c.request_changes(task, "Rework")
        self.report_done(task)
        self.c.send_to_review(task)
        with self.assertRaisesRegex(RuleError, "before"):
            record(self.c, old_review["id"], response_text(old_review))
        with self.assertRaises(RuleError):
            self.c.mark_ready(task)

    def test_reply_to_an_architecture_review_of_an_older_task_version_is_rejected(self):
        task = self.new_task()
        h = self.c.create_handoff(task)
        self.c.edit_task(task, description="A different task now")
        with self.assertRaisesRegex(RuleError, "changed"):
            record(self.c, h["id"], response_text(h))

    def test_reply_after_block_and_unblock_needs_a_new_handoff(self):
        task = self.in_progress()
        h = self.c.create_handoff(task)
        self.c.block(task, "Waiting")
        self.c.unblock(task)
        with self.assertRaises(RuleError):
            record(self.c, h["id"], response_text(h))
        self.report_done(task)
        self.assertEqual(self.c.send_to_review(task)["state"], "IN_REVIEW")


class M2NoFalseHumanAttribution(CoordinatorTestCase):
    """M-2: unauthenticated actions were recorded as the human owner's."""

    def test_unverified_actions_are_not_attributed_to_the_owner(self):
        self.c.pause_project(self.project, "Anyone can pause")
        actors = {e["action"]: e["actor"] for e in self.c.history()}
        self.assertNotEqual(actors["project_paused"], "human_owner")
        self.assertNotEqual(actors["owner_passphrase_set"], "human_owner")

    def test_every_owner_attributed_entry_was_passphrase_verified(self):
        task = self.completed()
        self.c.create_handoff(self.new_task("Other"))
        for event in self.c.history():
            if event["actor"] == "human_owner":
                self.assertIn("SIMULATED", json.dumps(event["details"]), event)
        self.assertNotIn("pasted in by the human", json.dumps(self.c.history(task_key=task)))


class M3PassphraseHandling(CoordinatorTestCase):
    """M-3: short passphrases, unlimited guessing, and overstated terminal claims."""

    def test_short_passphrases_are_refused(self):
        with self.assertRaises(AuthError):
            self.c.change_owner_passphrase(PASS, "eleven-char")
        self.c.change_owner_passphrase(PASS, "twelve-chars")

    def test_repeated_wrong_passphrases_lock_owner_actions_for_a_while(self):
        task = self.advance(self.new_task(), "AWAITING_APPROVAL")
        now = [1_000_000.0]
        self.c.now_seconds = lambda: now[0]
        for _ in range(3):
            with self.assertRaises(AuthError):
                self.c.approve(task, "wrong-passphrase")
        with self.assertRaisesRegex(AuthError, "wait"):
            self.c.approve(task, PASS)  # even the right passphrase waits
        now[0] += 3600
        self.assertEqual(self.c.approve(task, PASS)["state"], "APPROVED")

    def test_documentation_does_not_overstate_the_terminal_check(self):
        with open(os.path.join(ROOT, "docs", "COORDINATOR.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("so a script or AI agent driving the\n  command line cannot make an owner decision", text)
        self.assertIn("pseudo-terminal", text)


class L1ReplyParsing(unittest.TestCase):
    """L-1: quadratic fence matching and uncaught RecursionError."""

    def test_unclosed_fences_are_parsed_quickly(self):
        start = time.time()
        with self.assertRaises(ValidationError):
            ho.extract_response_object("```\nx" * 40_000)
        self.assertLess(time.time() - start, 2.0)

    def test_deeply_nested_json_is_a_validation_error(self):
        for text in ('{"a":' + "[" * 90_000 + "]" * 90_000 + "}",
                     "```json\n" + '{"a":' + "[" * 5_000 + "]" * 5_000 + "}\n```"):
            with self.assertRaises(ValidationError):
                ho.extract_response_object(text)


class L2DatabaseErrors(CoordinatorTestCase):
    """L-2: tracebacks on database errors, symlink-following backups, racy duplicate check."""

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--db", self.path, *argv])
        return code, out.getvalue()

    def test_locked_database_gives_a_plain_message(self):
        lock = sqlite3.connect(self.path, isolation_level=None, timeout=0)
        lock.execute("BEGIN IMMEDIATE")
        try:
            with mock.patch("dexter_coordinator.store.BUSY_TIMEOUT_SECONDS", 0.1, create=True):
                code, out = self.run_cli("project", "create", "Another")
        finally:
            lock.execute("ROLLBACK")
            lock.close()
        self.assertEqual(code, 1)
        self.assertIn("busy", out)

    def test_backup_refuses_a_dangling_symlink(self):
        target = os.path.join(self.dir, "elsewhere.db")
        link = os.path.join(self.dir, "link.db")
        os.symlink(target, link)
        with self.assertRaises(StoreError):
            self.c.backup(link)
        self.assertFalse(os.path.exists(target))

    def test_backup_into_a_missing_directory_is_a_plain_error(self):
        with self.assertRaises(StoreError):
            self.c.backup(os.path.join(self.dir, "missing", "x.db"))

    def test_duplicate_reply_race_is_a_rule_error(self):
        task = self.in_progress()
        h = self.c.create_handoff(task)
        record(self.c, h["id"], response_text(h))
        original = self.c.store.one

        def racing_one(statement, params=()):
            if statement.startswith("SELECT 1 FROM responses WHERE handoff_id"):
                return None  # another process has not finished writing yet
            return original(statement, params)

        self.c.store.one = racing_one
        with self.assertRaises(RuleError):
            record(self.c, h["id"], response_text(h))


class L3DatabaseStructure(unittest.TestCase):
    """L-3: a crafted database file with triggers or views was accepted."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dexter-audit-")
        self.path = os.path.join(self.dir, "x.db")
        Coordinator(self.path).close()

    def test_trigger_is_refused(self):
        sql(self.path, "CREATE TRIGGER sneaky AFTER INSERT ON events BEGIN UPDATE tasks SET state = 'COMPLETED'; END")
        with self.assertRaises(StoreError):
            Coordinator(self.path)

    def test_view_and_extra_table_are_refused(self):
        sql(self.path, "CREATE VIEW v AS SELECT 1")
        with self.assertRaises(StoreError):
            Coordinator(self.path)

    def test_changed_table_definition_is_refused(self):
        sql(self.path, "ALTER TABLE tasks ADD COLUMN extra TEXT")
        with self.assertRaises(StoreError):
            Coordinator(self.path)


class L4NoDatabasesInRepositories(unittest.TestCase):
    """L-4: a database inside the repository could be committed by accident."""

    def test_gitignore_covers_local_databases(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
            patterns = f.read().split()
        for pattern in ("*.db", "*.db-journal", "*.sqlite", ".dexter/"):
            self.assertIn(pattern, patterns)

    def test_new_database_inside_a_git_working_tree_is_refused(self):
        with tempfile.TemporaryDirectory() as repo:
            os.mkdir(os.path.join(repo, ".git"))
            with self.assertRaisesRegex(StoreError, "git"):
                Coordinator(os.path.join(repo, "sub", "try.db"))

    def test_documentation_does_not_suggest_a_database_in_the_repository(self):
        for name in ("COORDINATOR.md", "PHASE-3A-REPORT.md"):
            with open(os.path.join(ROOT, "docs", name), encoding="utf-8") as f:
                self.assertNotIn("--db ./", f.read())


class T1RealTerminalPath(unittest.TestCase):
    """T-1: the real passphrase prompt, driven through a pseudo-terminal (POSIX only).

    It also documents a limit: a program that can open a pseudo-terminal can type
    into the prompt, so the terminal check is not an authentication boundary.
    """

    @unittest.skipUnless(sys.platform != "win32" and hasattr(os, "fork"), "needs a POSIX pseudo-terminal")
    def test_init_through_a_pseudo_terminal(self):
        import pty

        def read_until(fd, marker, timeout=30):
            seen, deadline = b"", time.time() + timeout
            while marker not in seen and time.time() < deadline:
                ready, _, _ = select.select([fd], [], [], 0.5)
                if ready:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    seen += chunk
            return seen

        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "t.db")
            pid, fd = pty.fork()
            if pid == 0:  # pragma: no cover - child process
                os.chdir(ROOT)
                os.execvp(sys.executable, [sys.executable, "-m", "dexter_coordinator", "--db", db, "init"])
            output = read_until(fd, b"New owner passphrase:")
            os.write(fd, b"pty-test-passphrase\n")
            output += read_until(fd, b"again:")
            os.write(fd, b"pty-test-passphrase\n")
            output += read_until(fd, b"\x00", timeout=30)  # read to the end
            _, status = os.waitpid(pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode")
                             else os.WEXITSTATUS(status), 0, output.decode(errors="replace"))
            with Coordinator(db) as c:
                self.assertTrue(c.owner_is_set())


if __name__ == "__main__":
    unittest.main()


class T1OwnerSetupAndSignatures(CoordinatorTestCase):
    """T-1: owner set-up, passphrase changes, signatures and permissions under normal use."""

    def test_honest_workflow_passes_every_check(self):
        task = self.completed()
        self.c.mark_handoff_sent(self.c.create_handoff(self.in_progress(self.new_task("Second")))["id"], PASS)
        self.c.cancel(self.new_task("Third"), PASS, "Not needed")
        self.assertEqual(self.c.verify(passphrase=PASS), [])
        self.assertEqual(self.c.permission_warnings(), [])
        self.assertEqual(self.state(task), "COMPLETED")

    def test_changing_the_passphrase_keeps_old_signatures_valid(self):
        self.completed()
        self.c.change_owner_passphrase(PASS, "a-brand-new-passphrase")
        self.assertEqual(self.c.verify(passphrase="a-brand-new-passphrase"), [])
        with self.assertRaises(AuthError):
            self.c.verify(passphrase=PASS)

    def test_replaced_owner_record_is_noticed_by_the_real_owner(self):
        task = self.approved()
        attacker = make_coordinator(os.path.join(self.dir, "attacker.db"))
        attacker.set_owner_passphrase("attacker-passphrase")
        row = attacker.store.one("SELECT * FROM owner")
        attacker.close()
        sql(self.path, "UPDATE owner SET salt=?, cost=?, verifier=?, wrapped_key=? WHERE id=1",
            (row["salt"], row["cost"], row["verifier"], row["wrapped_key"]))
        with self.assertRaises(AuthError):
            self.c.cancel(task, PASS, "x")  # the real owner's passphrase stops working
        problems = self.c.verify(passphrase="attacker-passphrase")
        self.assertTrue(any("signature" in p for p in problems))  # old entries were signed with another key

    def test_new_database_is_private(self):
        if os.name != "posix":
            self.skipTest("POSIX permissions")
        mode = os.stat(self.path).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        os.chmod(self.path, 0o644)
        self.assertTrue(any("other users" in w for w in self.c.permission_warnings()))
        os.chmod(self.path, 0o600)

    def test_cli_init_and_change_passphrase(self):
        db = os.path.join(self.dir, "cli.db")
        answers = iter(["short", "short"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(cli, "read_passphrase", lambda p="": next(answers)):
            self.assertEqual(cli.main(["--db", db, "init"]), 1)
        self.assertIn("at least 12 characters", out.getvalue())
        for pair, expected in ((["first-passphrase-1", "different-one-22"], 1),
                               (["first-passphrase-1", "first-passphrase-1"], 0)):
            answers = iter(pair)
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(cli, "read_passphrase", lambda p="": next(answers)):
                self.assertEqual(cli.main(["--db", db, "init"]), expected)
        answers = iter(["first-passphrase-1", "second-passphrase-2", "second-passphrase-2"])
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(cli, "read_passphrase", lambda p="": next(answers)):
            self.assertEqual(cli.main(["--db", db, "owner", "change-passphrase"]), 0)
        with Coordinator(db) as c:
            self.assertEqual(c.verify(passphrase="second-passphrase-2"), [])
            history = c.history()
        self.assertEqual([e["actor"] for e in history], ["local_operator", "human_owner"])
