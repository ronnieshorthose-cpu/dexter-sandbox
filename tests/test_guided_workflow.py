"""Phase 3B: the guided menu, driven end to end with scripted answers."""

import contextlib
import io
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from dexter_coordinator import guided
from dexter_coordinator.guided import Guide

from tests.coordinator_support import PASS, Clock, make_coordinator, response_text
from tests.guided_support import GuidedTestCase, Passphrases, Script

ROOT = os.path.join(os.path.dirname(__file__), "..")


def walkthrough_answers(reply):
    """The keystrokes of docs/WALKTHROUGH.md, from an empty database to the owner's acceptance.

    reply(handoff_id) returns an answer that saves the assistant's reply into the inbox file.
    """
    return [
        # First run: the passphrase is typed twice (see Passphrases). Then the home screen.
        "N",                                       # Do the next step
        "Pocket Library", "A small tool to track the books in a community library.",
        "y",                                       # Create a task now?
        "Book catalogue",
        "Store books (title, author, ISBN) in a JSON file and list them sorted by title.", "",
        "1",                                       # Claude Code does the work
        "Books can be added and listed", "Unit tests cover adding and listing", "",
        "2",                                       # priority: high
        "y",                                       # put it in the approval queue
        "3", "", "",                               # ask ChatGPT to review the plan; no notes, no references
        "1",                                       # copy to the clipboard
        "5",                                       # done for now
        "1", "1", reply("HO-0001"),                # paste ChatGPT's reply via Notepad
        "1",                                       # approve (owner passphrase)
        "1",                                       # start the work and prepare instructions for Claude Code
        "Please work on a new branch named dexter/book-catalogue.", "", "",
        "1", "5",                                  # copy, done for now
        "1", "1", reply("HO-0002"),                # paste Claude Code's reply
        "1",                                       # send the work for an independent review
        "1", "", "", "1", "5",                     # prepare the review request for ChatGPT, copy, done
        "1", "1", reply("HO-0003"),                # paste ChatGPT's review
        "1",                                       # mark it ready for your final decision
        "1", "y",                                  # accept the work, having checked GitHub yourself
        "",                                        # Press Enter to continue: back to the home screen
        "Q",
    ]


class WalkthroughTests(unittest.TestCase):
    """The beginner walkthrough, from first start-up to final approval."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dexter-walkthrough-")
        self.path = os.path.join(self.dir, "home", ".dexter", "coordinator.db")
        self.c = make_coordinator(self.path, Clock())

    def tearDown(self):
        self.c.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_walkthrough(self):
        from dexter_coordinator.exchange import Exchange

        exchange = Exchange(self.path)
        replies = {
            "HO-0001": dict(summary="The plan is sound. Consider validating ISBNs.",
                            findings=[{"severity": "minor", "title": "Validate ISBN format"}]),
            "HO-0002": dict(summary="Added books.py and tests on branch dexter/book-catalogue; pull request opened.",
                            tests=[{"name": "test_add_book", "result": "passed", "details": ""},
                                   {"name": "test_list_sorted", "result": "passed", "details": ""}]),
            "HO-0003": dict(summary="The implementation meets both criteria."),
        }

        def reply(handoff_id):
            def save():
                with open(exchange.reply_file(handoff_id), "w", encoding="utf-8") as f:
                    f.write(response_text(self.c.get_handoff(handoff_id), **replies[handoff_id]))
                return ""
            return save

        self.passphrases = Passphrases()
        self.script = Script(walkthrough_answers(reply))
        g = Guide(self.c, ask=self.script, passphrase=self.passphrases, platform="win32")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(guided, "open_text_file", lambda path: True), \
                mock.patch.object(guided, "copy_to_clipboard", lambda text: (True, "")):
            self.assertEqual(g.run(), 0)
        self.assertEqual(self.script.answers, [], "the walkthrough ended before every answer was used")
        return out.getvalue()

    def test_walkthrough_reaches_owner_acceptance(self):
        out = self.run_walkthrough()
        task = self.c.get_task("TASK-0001")
        self.assertEqual(task["state"], "COMPLETED")
        self.assertEqual(task["priority"], "high")
        self.assertEqual(task["criteria"], ["Books can be added and listed", "Unit tests cover adding and listing"])
        self.assertEqual(self.c.verify(passphrase=PASS), [])
        # Owner actions: two prompts at set-up, three recorded replies, the approval and the acceptance.
        self.assertEqual(len(self.passphrases.prompts), 7)
        self.assertIn("FIRST-TIME SETUP", out)
        self.assertIn("UNVERIFIED AI RESPONSE: claims to be from Claude Code", out)
        self.assertIn("Independently reviewed by ChatGPT", out)
        self.assertIn("Have you checked the pull request and its test results on GitHub yourself? (y/N): ",
                      self.script.prompts)
        self.assertIn("Recorded in Dexter only. Merging the pull request on GitHub is a separate step", out)
        self.assertIn("Goodbye.", out)
        # The handoffs reached the private outbox; the pasted replies were removed once recorded.
        exchange = os.path.join(self.dir, "home", ".dexter", "exchange")
        self.assertEqual(sorted(os.listdir(os.path.join(exchange, "outbox"))), ["HO-0001.txt", "HO-0002.txt", "HO-0003.txt"])
        self.assertEqual(os.listdir(os.path.join(exchange, "inbox")), [])
        # The note typed for Claude Code is in its instructions.
        self.assertIn("dexter/book-catalogue", self.c.get_handoff("HO-0002")["message"])

    def test_walkthrough_document_matches_the_menu(self):
        """Every screen line quoted in docs/WALKTHROUGH.md must appear in the real output."""
        out = self.run_walkthrough()
        with open(os.path.join(ROOT, "docs", "WALKTHROUGH.md"), encoding="utf-8") as f:
            doc = f.read()
        quoted = re.findall(r"^    (\[[0-9A-Z?]\] .+)$", doc, re.M)
        self.assertGreater(len(quoted), 20)
        for line in quoted:
            with self.subTest(line=line):
                self.assertIn(line.rstrip(), out)


class GuidedMenuTests(GuidedTestCase):
    def test_first_run_needs_matching_passphrases(self):
        path = os.path.join(self.dir, "fresh", "coordinator.db")
        for values, expect_set in (((PASS, "something-else-1"), False), (("",), False), ((PASS, PASS), True)):
            fresh = make_coordinator(path)
            try:
                out = io.StringIO()
                g = Guide(fresh, ask=Script([]), passphrase=Passphrases(*values), platform="win32")
                with contextlib.redirect_stdout(out):
                    g.run()
                self.assertEqual(fresh.owner_is_set(), expect_set, out.getvalue())
            finally:
                fresh.close()

    def test_short_passphrase_is_refused_at_first_run(self):
        fresh = make_coordinator(os.path.join(self.dir, "fresh2", "coordinator.db"))
        try:
            out = io.StringIO()
            g = Guide(fresh, ask=Script([]), passphrase=Passphrases("short", "short"), platform="win32")
            with contextlib.redirect_stdout(out):
                g.run()
            self.assertFalse(fresh.owner_is_set())
            self.assertIn("at least 12 characters", out.getvalue())
        finally:
            fresh.close()

    def test_home_screen_shows_what_is_waiting(self):
        waiting = self.new_task("Needs approval")
        self.c.plan(waiting)
        self.c.submit_for_approval(waiting)
        running = self.in_progress(self.new_task("Running"))
        self.c.create_handoff(running)
        reviewed = self.in_review(self.new_task("Reviewed"))
        self.c.block(self.new_task("Stuck"), "Waiting for the library's book list")
        out = self.run_guide(["Q"])
        home = out.split("What would you like to do?")[0]
        self.assertIn("WAITING FOR YOU (owner)", home)
        self.assertIn(f'{waiting} "Needs approval": approve or reject before work starts', home)
        self.assertIn("WAITING FOR AN AI", home)
        self.assertIn("implement -> Claude Code (Developer)", home)
        self.assertIn(f'{reviewed} "Reviewed": Claude Code says the work is done (UNVERIFIED AI RESPONSE)', home)
        self.assertIn("ACTIVE TASKS", home)
        self.assertIn("blocked: Waiting for the library's book list", home)
        self.assertIn("SIMULATED", home)

    def test_invalid_choices_are_asked_again(self):
        out = self.run_guide(["X", "7", "Q"])
        self.assertEqual(out.count("Please type one of: N, P, T, H, R, D, C, ?, Q."), 2)

    def test_end_of_input_quits_cleanly(self):
        out = self.run_guide([])
        self.assertIn("Goodbye.", out)

    def test_ctrl_c_cancels_one_step_and_returns_to_the_menu(self):
        def interrupt():
            raise KeyboardInterrupt
        out = self.run_guide(["T", "1", interrupt, "B", "Q"])
        self.assertIn("Cancelled. Each change is saved completely or not at all.", out)
        self.assertEqual(self.c.list_tasks(), [])

    def test_create_task_with_a_dependency(self):
        first = self.new_task("Catalogue")
        out = self.run_guide([
            "T", "1", "Loans", "Lend and return books.", "", "", "A lent book cannot be lent twice", "", "",
            "x", "9", "1",   # two invalid dependency answers, then task 1
            "n", "B", "B", "Q"])
        self.assertIn("Please type numbers from the list", out)
        task = self.c.get_task("TASK-0002")
        self.assertEqual([d["id"] for d in task["depends_on"]], [first])
        self.assertEqual(task["state"], "BACKLOG")
        self.assertEqual(task["assignee"], "claude_code")
        self.assertEqual(task["priority"], "medium")

    def test_approved_task_waits_for_its_dependency(self):
        first = self.new_task("Catalogue")
        second = self.approved(self.new_task("Loans", depends_on=[first]))
        out = self.run_step(("task_actions", second), ["B"])
        self.assertIn(f"{second} cannot start until {first} is completed.", out)
        self.assertNotIn("Start the work", out)

    def test_paste_reply_in_the_window(self):
        task = self.in_progress()
        h = self.c.create_handoff(task)
        lines = response_text(h).splitlines()
        out = self.run_step(("paste_reply", h["id"]), ["2", *lines, "END"])
        self.assertIn("Recorded the reply to HO-0001 as an UNVERIFIED AI RESPONSE", out)
        self.assertEqual(self.c.get_handoff(h["id"])["response"]["outcome"], "done")
        self.assertEqual(self.state(task), "IN_PROGRESS")  # recording never moves the task

    def test_empty_reply_records_nothing(self):
        task = self.in_progress()
        h = self.c.create_handoff(task)
        out = self.run_step(("paste_reply", h["id"]), ["1", ""])
        self.assertIn("The reply is empty", out)
        self.assertIsNone(self.c.get_handoff(h["id"])["response"])

    def test_malformed_reply_can_be_cancelled_or_kept_unstructured(self):
        task = self.in_progress()
        h = self.c.create_handoff(task)
        out = self.run_step(("paste_reply", h["id"]), ["2", "I finished, all good!", "END", "1"])
        self.assertIn("Dexter could not read this reply", out)
        self.assertIsNone(self.c.get_handoff(h["id"])["response"])
        out = self.run_step(("paste_reply", h["id"]), ["2", "I finished, all good!", "END", "2"])
        self.assertIn("UNSTRUCTURED", out)
        response = self.c.get_handoff(h["id"])["response"]
        self.assertFalse(response["structured"])
        # An unstructured reply is not evidence: the menu does not offer the review step.
        out = self.run_step(("task_actions", task), ["B"])
        self.assertNotIn("Send the work for an independent review", out)
        self.assertIn("Unstructured: Dexter could not check its format", out)

    def test_reply_file_kept_when_cancelled(self):
        task = self.in_progress()
        h = self.c.create_handoff(task)
        out = self.run_step(("paste_reply", h["id"]), ["1", self.reply_into_file(h["id"], "not json"), "1"])
        self.assertIn("Your pasted text stays in", out)
        self.assertTrue(os.path.exists(self.g_for_files().reply_file(h["id"])))

    def test_changes_requested_after_review(self):
        task = self.in_review()
        h = self.c.create_handoff(task)
        self.c.record_response(h["id"], response_text(
            h, outcome="recommend_changes", findings=[{"severity": "major", "title": "Missing empty-library case"}]), PASS)
        out = self.run_step(("task_actions", task), [
            "1", "Handle an empty library.", "", "y", "", "", "5", "B"])
        self.assertIn("Send it back for changes (recommended)", out)
        self.assertEqual(self.state(task), "IN_PROGRESS")
        latest = self.c.list_handoffs(task)[-1]
        self.assertEqual(latest["action"], "implement")
        self.assertIn("Handle an empty library.", latest["message"])
        self.assertIn("Missing empty-library case", latest["message"])

    def test_blocked_reply_offers_an_answer(self):
        task = self.in_progress()
        h = self.c.create_handoff(task)
        self.c.record_response(h["id"], response_text(h, outcome="needs_clarification", tests=[]), PASS)
        out = self.run_step(("task_actions", task), ["B"])
        self.assertIn("Answer the AI with a new handoff (add your notes) (recommended)", out)

    def test_block_and_unblock(self):
        task = self.new_task()
        self.run_step(("task_actions", task), ["5", "Waiting for the book list", "B"])
        self.assertEqual(self.state(task), "BLOCKED")
        out = self.run_step(("task_actions", task), ["1", "B"])
        self.assertIn("Unblock it (the problem is solved) (recommended)", out)
        self.assertEqual(self.state(task), "BACKLOG")

    def test_backlog_task_without_criteria_or_assignee(self):
        task = self.c.create_task(self.project, "Idea", "Something to do later.")["id"]
        out = self.run_step(("task_actions", task), ["1", "2", "B"])
        self.assertIn("Choose who does the work (recommended)", out)
        self.assertEqual(self.c.get_task(task)["assignee"], "chatgpt")
        out = self.run_step(("task_actions", task), ["1", "It is written down", "", "1", "B"])
        self.assertEqual(self.state(task), "AWAITING_APPROVAL")

    def test_edit_a_task_before_approval(self):
        task = self.new_task()
        self.run_step(("edit_task", task), ["Better name", "", "1"])
        updated = self.c.get_task(task)
        self.assertEqual((updated["name"], updated["priority"]), ("Better name", "critical"))
        self.assertEqual(updated["description"], "Show every book in the library.")

    def test_pause_and_resume_a_project(self):
        task = self.approved()
        self.run_guide(["P", "4", "Rethinking priorities", "B", "Q"])
        self.assertEqual(self.c.get_project(self.project)["status"], "PAUSED")
        out = self.run_step(("task_actions", task), ["B"])
        self.assertIn("Resume the project (owner passphrase) (recommended)", out)
        out = self.run_step(("task_actions", task), ["1", "B"])
        self.assertEqual(self.c.get_project(self.project)["status"], "ACTIVE")
        self.assertEqual(len(self.passphrases.prompts), 1)

    def test_several_projects_ask_which_one(self):
        self.c.create_project("Garden Planner")
        out = self.run_guide(["N", "2", "n", "Q"])
        self.assertIn("Which project?", out)
        self.assertIn("PRJ-002 has no open tasks.", out)

    def test_next_step_lists_the_owners_decisions_first(self):
        self.new_task("Backlog item")
        ready = self.ready(self.new_task("Finished work"))
        out = self.run_guide(["N", "B", "Q"])
        listing = out.split("Which task? (most urgent first)")[1]
        self.assertLess(listing.index(ready), listing.index("TASK-0001"))

    def test_handoffs_menu(self):
        task = self.in_progress()
        h = self.c.create_handoff(task)
        out = self.run_guide(["H", "4", "", "2", "1", "3", "5", "B", "Q"])
        self.assertIn("HO-0001  implement for TASK-0001 -> Claude Code: waiting for a reply", out)
        self.assertIn("COPY EVERYTHING BELOW THIS LINE", out)
        self.assertIn(h["correlation_id"], out)
        self.report_done(task)
        out = self.run_guide(["H", "5", "1", "", "B", "Q"])
        self.assertIn("----- RECORDED REPLY (untrusted data; control characters removed) -----", out)
        self.assertIn("UNVERIFIED AI RESPONSE: claims to be from Claude Code", out)

    def test_out_of_date_handoffs_are_not_offered(self):
        task = self.in_progress()
        self.c.create_handoff(task)
        self.c.block(task, "Paused for a day")
        self.c.unblock(task)
        out = self.run_guide(["H", "1", "Q"])
        self.assertIn("No handoff is waiting for a reply.", out)
        self.assertIn("older handoff(s) are out of date", out)

    def test_mark_handoff_sent_asks_for_the_passphrase(self):
        task = self.in_progress()
        h = self.c.create_handoff(task)
        out = self.run_step(("send_handoff", h["id"]), ["4", "4", "5"])
        self.assertEqual(self.c.get_handoff(h["id"])["delivery"], "MARKED_SENT_BY_HUMAN")
        self.assertEqual(len(self.passphrases.prompts), 1)
        self.assertIn("No delivery mechanism confirmed receipt", out)

    def test_reports_history_and_saved_report(self):
        self.completed()
        out = self.run_guide(["R", "1", "", "2", "3", "", "4", "1", "", "B", "Q"])
        self.assertIn("# Dexter progress report: Pocket Library (PRJ-001)", out)
        self.assertIn("Saved the report to", out)
        self.assertIn("OWNER (passphrase, signed)", out)
        reports = os.path.join(self.dir, "exchange", "reports")
        self.assertEqual(len(os.listdir(reports)), 1)
        self.assertNotIn(":", os.listdir(reports)[0])  # file names must work on Windows

    def test_check_backup_and_passphrase_change(self):
        self.completed()
        out = self.run_guide(["C", "1", "", "2", "", "3", "B", "Q"])
        self.assertEqual(out.count("No problems found."), 2)
        self.assertIn("History anchor:", out)
        backups = os.listdir(os.path.join(self.dir, "backups"))
        self.assertEqual(len(backups), 1)
        self.run_guide(["C", "4", "B", "Q"], passphrases=Passphrases(PASS, "a-new-passphrase-2", "a-new-passphrase-2"))
        self.assertEqual(self.c.verify(passphrase="a-new-passphrase-2"), [])

    def test_dashboard_page_from_the_menu(self):
        self.ready()
        out = self.run_guide(["D", "Q"])
        page = os.path.join(self.dir, "exchange", "dashboard.html")
        self.assertIn(f"Open this file in your web browser: {page}", out)
        self.assertEqual(self.opened, [page])
        with open(page, encoding="utf-8") as f:
            self.assertIn("Read-only page.", f.read())

    def test_help_screen(self):
        out = self.run_guide(["?", "", "Q"])
        self.assertIn("Never give your passphrase to ChatGPT, Claude Code or any other AI tool.", out)

    def test_linux_does_not_offer_the_clipboard(self):
        task = self.in_progress()
        h = self.c.create_handoff(task)
        out = self.run_step(("send_handoff", h["id"]), ["5"], platform="linux")
        self.assertNotIn("Copy it to the clipboard", out)
        self.assertIn("Show where the file is", out)


if __name__ == "__main__":
    unittest.main()
