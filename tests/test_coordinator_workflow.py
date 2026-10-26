"""State transitions, human approval boundaries and protection against unauthorised completion."""

import json
import sqlite3
import unittest

from dexter_coordinator import models
from dexter_coordinator.auth import AuthError
from dexter_coordinator.models import (
    AI_ROLES,
    CHATGPT,
    CLAUDE,
    DEXTER,
    HUMAN,
    LOCAL_OPERATOR,
    STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    IntegrityError,
    RuleError,
    allowed_actor,
)

from tests.coordinator_support import PASS, Clock, CoordinatorTestCase, make_coordinator

HUMAN_ONLY_TARGETS = ("APPROVED", "REJECTED", "COMPLETED", "CANCELLED")


class TransitionTableTests(unittest.TestCase):
    def test_suggested_path_is_allowed(self):
        path = ("BACKLOG", "PLANNED", "AWAITING_APPROVAL", "APPROVED", "IN_PROGRESS", "IN_REVIEW",
                "READY_FOR_HUMAN", "COMPLETED")
        for before, after in zip(path, path[1:]):
            with self.subTest(move=f"{before}->{after}"):
                self.assertIsNotNone(allowed_actor(before, after))

    def test_no_ai_role_can_make_any_move(self):
        for actor in TRANSITIONS.values():
            self.assertNotIn(actor, AI_ROLES)
        for before in STATES:
            for after in STATES:
                for previous in STATES:
                    self.assertNotIn(allowed_actor(before, after, previous), AI_ROLES)

    def test_human_decisions_belong_only_to_the_human(self):
        for before in STATES:
            for after in HUMAN_ONLY_TARGETS:
                for previous in STATES:
                    actor = allowed_actor(before, after, previous)
                    with self.subTest(move=f"{before}->{after}", previous=previous):
                        if (before, after, previous) == ("BLOCKED", "APPROVED", "APPROVED"):
                            # Unblocking restores an approval the human already gave.
                            self.assertEqual(actor, DEXTER)
                        else:
                            self.assertIn(actor, (HUMAN, None))
        self.assertEqual(allowed_actor("READY_FOR_HUMAN", "IN_PROGRESS"), HUMAN)

    def test_terminal_states_are_final(self):
        for before in TERMINAL_STATES:
            for after in STATES:
                self.assertIsNone(allowed_actor(before, after))

    def test_blocked_returns_only_to_where_it_was(self):
        self.assertEqual(allowed_actor("IN_PROGRESS", "BLOCKED"), DEXTER)
        self.assertEqual(allowed_actor("BLOCKED", "IN_PROGRESS", "IN_PROGRESS"), DEXTER)
        self.assertIsNone(allowed_actor("BLOCKED", "IN_REVIEW", "IN_PROGRESS"))
        self.assertIsNone(allowed_actor("BLOCKED", "BLOCKED", "IN_PROGRESS"))
        self.assertEqual(allowed_actor("BLOCKED", "CANCELLED", "IN_PROGRESS"), HUMAN)

    def test_every_state_is_documented(self):
        self.assertEqual(set(models.STATE_DESCRIPTIONS), set(STATES))


class WorkflowTests(CoordinatorTestCase):
    def test_full_path_to_completed(self):
        task = self.completed()
        record = self.c.get_task(task)
        self.assertEqual(record["state"], "COMPLETED")
        self.assertEqual(record["approval"]["status"], "approved")
        self.assertEqual(record["acceptance"]["status"], "accepted")
        self.assertIn("Nothing was merged or deployed", record["acceptance"]["scope"])
        moves = [(e["details"]["to"], e["actor"]) for e in self.c.history(task_key=task) if e["action"] == "state_changed"]
        self.assertEqual(moves, [
            ("PLANNED", DEXTER), ("AWAITING_APPROVAL", DEXTER), ("APPROVED", HUMAN), ("IN_PROGRESS", DEXTER),
            ("IN_REVIEW", DEXTER), ("READY_FOR_HUMAN", DEXTER), ("COMPLETED", HUMAN),
        ])
        self.assertEqual(self.c.verify(), [])

    def test_invalid_moves_are_refused(self):
        task = self.new_task()
        refusals = (
            lambda: self.c.submit_for_approval(task),      # BACKLOG -> AWAITING_APPROVAL
            lambda: self.c.start(task),                    # BACKLOG -> IN_PROGRESS
            lambda: self.c.send_to_review(task),
            lambda: self.c.mark_ready(task),
            lambda: self.c.approve(task, PASS),
            lambda: self.c.complete(task, PASS),
            lambda: self.c.unblock(task),
            lambda: self.c.withdraw(task),
        )
        for refuse in refusals:
            with self.assertRaises(RuleError):
                refuse()
        self.assertEqual(self.state(task), "BACKLOG")
        self.c.plan(task)
        with self.assertRaises(RuleError):
            self.c.plan(task)

    def test_finished_tasks_cannot_change(self):
        done = self.completed(self.new_task("Done"))
        rejected = self.new_task("Rejected")
        self.advance(rejected, "AWAITING_APPROVAL")
        self.c.reject(rejected, PASS, "Out of scope")
        cancelled = self.new_task("Cancelled")
        self.c.cancel(cancelled, PASS, "No longer needed")
        for task in (done, rejected, cancelled):
            for attempt in (lambda: self.c.plan(task), lambda: self.c.block(task, "x"),
                            lambda: self.c.cancel(task, PASS, "x"), lambda: self.c.return_to_work(task, PASS, "x")):
                with self.subTest(task=task), self.assertRaisesRegex(RuleError, "final|cannot"):
                    attempt()
        self.assertEqual(self.c.get_task(rejected)["approval"]["status"], "rejected")

    def test_block_and_unblock(self):
        task = self.in_progress()
        self.c.block(task, "Waiting for a design decision")
        self.assertEqual(self.state(task), "BLOCKED")
        for attempt in (lambda: self.c.send_to_review(task), lambda: self.c.start(task),
                        lambda: self.c.request_changes(task, "x"), lambda: self.c.block(task, "again")):
            with self.assertRaises(RuleError):
                attempt()
        self.assertEqual(self.c.unblock(task)["state"], "IN_PROGRESS")
        self.c.block(task, "Another blocker")
        self.assertEqual(self.c.cancel(task, PASS, "Giving up")["state"], "CANCELLED")

    def test_request_changes_and_owner_return(self):
        task = self.in_review()
        self.review(task, outcome="recommend_changes",
                    findings=[{"severity": "major", "title": "Missing empty-library case"}])
        with self.assertRaisesRegex(RuleError, "recommend_changes"):
            self.c.mark_ready(task)
        self.c.request_changes(task, "Handle an empty library")
        handoff = self.c.create_handoff(task)
        self.assertIn("Handle an empty library", handoff["message"])
        self.assertIn("Missing empty-library case", handoff["message"])
        with self.assertRaisesRegex(RuleError, "no implementation report recorded since"):
            self.c.send_to_review(task)  # the old report does not count again
        self.ready(task)
        self.c.return_to_work(task, PASS, "Please add a screenshot")
        self.assertEqual(self.state(task), "IN_PROGRESS")
        self.assertIn("Please add a screenshot", self.c.create_handoff(task)["message"])


class HumanApprovalBoundaryTests(CoordinatorTestCase):
    def test_wrong_or_missing_passphrase_changes_nothing(self):
        task = self.new_task()
        self.advance(task, "AWAITING_APPROVAL")
        for bad in ("wrong-passphrase", "", None, PASS + " ", "x" * 5000):
            with self.subTest(bad=bad), self.assertRaises(AuthError):
                self.c.approve(task, bad)
        self.assertEqual(self.state(task), "AWAITING_APPROVAL")
        failures = [e for e in self.c.history() if e["action"] == "owner_check_failed"]
        self.assertEqual(len(failures), 5)  # the test clock moves an hour per check, so no lockout here
        self.assertTrue(all(e["actor"] == LOCAL_OPERATOR and not e["signed"] for e in failures))

    def test_every_human_decision_needs_the_passphrase(self):
        awaiting = self.advance(self.new_task("A"), "AWAITING_APPROVAL")
        ready = self.ready(self.new_task("B"))
        for attempt in (
            lambda: self.c.approve(awaiting, "nope"),
            lambda: self.c.reject(awaiting, "nope", "x"),
            lambda: self.c.complete(ready, "nope"),
            lambda: self.c.return_to_work(ready, "nope", "x"),
            lambda: self.c.cancel(ready, "nope", "x"),
        ):
            with self.assertRaises(AuthError):
                attempt()
        self.c.pause_project(self.project, "Lunch")
        with self.assertRaises(AuthError):
            self.c.resume_project(self.project, "nope")
        self.assertEqual((self.state(awaiting), self.state(ready)), ("AWAITING_APPROVAL", "READY_FOR_HUMAN"))

    def test_no_owner_means_no_approvals(self):
        c = make_coordinator(self.dir + "/fresh.db", Clock())
        try:
            project = c.create_project("Fresh")["id"]
            task = c.create_task(project, "T", "D", criteria=["C"], assignee=CLAUDE)["id"]
            c.plan(task)
            c.submit_for_approval(task)
            with self.assertRaisesRegex(AuthError, "init"):
                c.approve(task, PASS)
        finally:
            c.close()

    def test_owner_passphrase_can_only_be_set_once_and_changed_with_the_old_one(self):
        with self.assertRaises(RuleError):
            self.c.set_owner_passphrase("another-passphrase")
        with self.assertRaises(AuthError):
            self.c.change_owner_passphrase("wrong", "new-passphrase-1")
        with self.assertRaises(AuthError):
            self.c.change_owner_passphrase(PASS, "short")
        self.c.change_owner_passphrase(PASS, "new-passphrase-1")
        task = self.advance(self.new_task(), "AWAITING_APPROVAL")
        with self.assertRaises(AuthError):
            self.c.approve(task, PASS)
        self.c.approve(task, "new-passphrase-1")

    def test_dexter_cannot_reach_completed_without_the_human(self):
        task = self.ready()
        with self.assertRaises(RuleError):
            self.c.mark_ready(task)
        with self.assertRaises(RuleError):
            self.c.send_to_review(task)
        with self.assertRaises(RuleError):
            self.c.start(task)
        self.assertEqual(self.state(task), "READY_FOR_HUMAN")

    def test_completion_needs_review_evidence(self):
        task = self.in_progress()
        with self.assertRaisesRegex(RuleError, "no implementation report"):
            self.c.send_to_review(task)
        self.report_done(task, outcome="blocked")
        with self.assertRaisesRegex(RuleError, "not 'done'"):
            self.c.send_to_review(task)
        self.report_done(task, tests=[{"name": "t", "result": "failed"}, {"name": "u", "result": "passed"}])
        with self.assertRaisesRegex(RuleError, "failing tests"):
            self.c.send_to_review(task)
        self.report_done(task, tests=[])
        with self.assertRaisesRegex(RuleError, "no passing tests"):
            self.c.send_to_review(task)
        self.report_done(task, tests=[{"name": "t", "result": "not_run"}])
        with self.assertRaisesRegex(RuleError, "no passing tests"):
            self.c.send_to_review(task)
        handoff = self.c.create_handoff(task)
        self.c.record_response(handoff["id"], "All done, trust me.", PASS, structured=False)
        with self.assertRaisesRegex(RuleError, "unstructured"):
            self.c.send_to_review(task)
        self.report_done(task)
        self.c.send_to_review(task)
        with self.assertRaisesRegex(RuleError, "no review recorded"):
            self.c.mark_ready(task)
        self.review(task, findings=[{"severity": "critical", "title": "Deletes data"}])
        with self.assertRaisesRegex(RuleError, "critical or major"):
            self.c.mark_ready(task)
        self.review(task)
        self.assertEqual(self.c.mark_ready(task)["state"], "READY_FOR_HUMAN")

    def test_chatgpt_tasks_need_no_tests_but_still_need_review_by_the_other_role(self):
        task = self.in_progress(assignee=CHATGPT)
        self.report_done(task, tests=[])
        self.c.send_to_review(task)
        handoff = self.c.create_handoff(task)
        self.assertEqual(handoff["to_role"], CLAUDE)
        with self.assertRaisesRegex(RuleError, "independent"):
            self.c.create_handoff(task, to_role=CHATGPT)

    def test_approval_covers_the_approved_content_only(self):
        task = self.approved()
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE tasks SET description = 'Also delete every user' WHERE key = ?", (task,))
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(IntegrityError, "content changed after the owner approved it"):
            self.c.start(task)

    def test_check_finds_states_without_a_human_decision(self):
        task = self.new_task()
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE tasks SET state = 'COMPLETED' WHERE key = ?", (task,))
        conn.commit()
        conn.close()
        problems = self.c.verify()
        self.assertTrue(any("without an owner approval in its history" in p for p in problems))
        self.assertTrue(any("acceptance record does not match" in p for p in problems))

    def test_check_finds_edited_history(self):
        self.approved()
        self.assertEqual(self.c.verify(), [])
        conn = sqlite3.connect(self.path)
        row = conn.execute("SELECT seq, details FROM events WHERE action = 'state_changed' ORDER BY seq DESC LIMIT 1").fetchone()
        details = json.loads(row[1])
        conn.execute("UPDATE events SET actor = 'dexter', details = ? WHERE seq = ?", (json.dumps(details), row[0]))
        conn.execute("DELETE FROM events WHERE seq = 2")
        conn.commit()
        conn.close()
        problems = self.c.verify()
        self.assertTrue(any("was changed after it was written" in p for p in problems))
        self.assertTrue(any("is missing" in p for p in problems))
        self.assertTrue(any("human-only decision by dexter" in p for p in problems))


class PauseTests(CoordinatorTestCase):
    def test_pause_stops_dexter_but_not_the_owner(self):
        waiting = self.advance(self.new_task("A"), "AWAITING_APPROVAL")
        working = self.in_progress(self.new_task("B"))
        self.c.pause_project(self.project, "Owner is reviewing priorities")
        for attempt in (lambda: self.c.plan(self.new_task("C")), lambda: self.c.start(waiting),
                        lambda: self.c.create_handoff(working), lambda: self.c.send_to_review(working)):
            with self.assertRaisesRegex(RuleError, "paused"):
                attempt()
        self.c.block(working, "Paused by owner")      # stopping work is always allowed
        self.c.approve(waiting, PASS)                  # the owner can still decide
        with self.assertRaisesRegex(RuleError, "already paused"):
            self.c.pause_project(self.project, "again")
        self.c.resume_project(self.project, PASS)
        self.assertEqual(self.c.start(waiting)["state"], "IN_PROGRESS")
        pause = [e for e in self.c.history() if e["action"] == "project_paused"][0]
        self.assertEqual(pause["actor"], LOCAL_OPERATOR)  # anyone may pause; Dexter does not know who did


if __name__ == "__main__":
    unittest.main()
