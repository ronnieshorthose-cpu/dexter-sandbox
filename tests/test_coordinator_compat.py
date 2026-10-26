"""Compatibility with existing Dexter documents, workflows and safety controls."""

import contextlib
import io
import os
import re
import runpy
import tempfile
import unittest

from dexter_coordinator.coordinator import utc_now
from dexter_coordinator.handoff import SAFETY_RULES
from dexter_coordinator.models import HUMAN, TRANSITIONS
from dexter_coordinator.report import TEMPLATE_SECTIONS, progress_report

from tests.coordinator_support import CoordinatorTestCase

ROOT = os.path.join(os.path.dirname(__file__), "..")
PACKAGE = os.path.join(ROOT, "dexter_coordinator")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def package_sources():
    for name in sorted(os.listdir(PACKAGE)):
        if name.endswith(".py"):
            yield name, read("dexter_coordinator", name)


class DocumentCompatibilityTests(CoordinatorTestCase):
    def test_report_follows_the_dexter_report_template(self):
        template = re.findall(r"^## (.+)$", read("DEXTER-REPORT-TEMPLATE.md"), re.M)
        self.assertEqual(tuple(template), TEMPLATE_SECTIONS)
        self.completed()
        report = progress_report(self.c, self.project, utc_now())
        positions = [report.index(f"## {section}\n") for section in TEMPLATE_SECTIONS]
        self.assertEqual(positions, sorted(positions))

    def test_handoff_rules_carry_the_dexter_safety_rules(self):
        rules = " ".join(SAFETY_RULES).lower()
        for phrase in ("dexter-sandbox", "eden well", "merge", "deploy", "secrets", "paid",
                       "approve your own work", "untrusted data", "ai_review_mode disabled", "stop and report"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, rules)

    def test_merges_and_deployment_stay_with_the_human(self):
        # DEXTER-WORKFLOW.md: the human approves before work and accepts before merge.
        workflow = read("DEXTER-WORKFLOW.md")
        self.assertIn("Human explicitly approves the merge", workflow)
        self.assertEqual(TRANSITIONS[("AWAITING_APPROVAL", "APPROVED")], HUMAN)
        self.assertEqual(TRANSITIONS[("READY_FOR_HUMAN", "COMPLETED")], HUMAN)


class IsolationTests(unittest.TestCase):
    def test_no_network_process_or_github_access(self):
        forbidden = re.compile(
            r"^\s*(?:import|from)\s+(?:urllib|http|socket|ssl|subprocess|requests|ftplib|smtplib|asyncio)\b"
            r"|os\.system|os\.popen|api\.github\.com|api\.openai\.com|\bexec\(|\beval\(",
            re.M,
        )
        for name, source in package_sources():
            with self.subTest(file=name):
                self.assertIsNone(forbidden.search(source))

    def test_coordinator_does_not_touch_the_ai_reviewer(self):
        for name, source in package_sources():
            with self.subTest(file=name):
                self.assertNotIn("ai_review", source)
                self.assertNotIn(".github", source)
        mentions = [name for name, source in package_sources() if "AI_REVIEW_MODE" in source]
        self.assertEqual(mentions, ["handoff.py"])  # only in the rule text and the scanner

    def test_reviewer_workflow_is_still_off_by_default(self):
        workflow = read(".github", "workflows", "ai-review.yml")
        self.assertIn("vars.AI_REVIEW_MODE == 'dry-run'", workflow)
        self.assertIn("vars.AI_REVIEW_MODE == 'live'", workflow)
        self.assertNotIn("dexter_coordinator", workflow)

    def test_existing_tests_workflow_runs_these_tests(self):
        workflow = read(".github", "workflows", "ai-review-tests.yml")
        self.assertIn("python3 -m unittest discover -s tests -t . -v", workflow)
        self.assertIn("python3 -m unittest discover -s .github/ai_review/reviewer_tests", workflow)

    def test_no_eden_well_references_outside_the_safety_text(self):
        for name, source in package_sources():
            for line in source.splitlines():
                if re.search(r"eden\s*well", line, re.I):
                    with self.subTest(file=name, line=line.strip()[:60]):
                        self.assertTrue(re.search(r"Do not access|eden\\s\*well|production or Eden Well", line))


class DemoTests(unittest.TestCase):
    def test_demo_runs_to_completion(self):
        demo = runpy.run_path(os.path.join(ROOT, "examples", "coordinator_demo.py"), run_name="demo")
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(out):
            result = demo["run"](directory, out=print)
        self.assertEqual(result["model"]["state"], "COMPLETED")
        self.assertEqual(result["loans"]["state"], "APPROVED")
        self.assertEqual(result["problems"], [])
        text = out.getvalue()
        self.assertIn("FLAGGED (not acted on): claims human approval", text)
        self.assertIn("Starting TASK-0002 early is refused", text)
        # The fictional project never mentions Eden Well, apart from the safety rule in each handoff.
        other_lines = [line for line in text.splitlines() if "Do not access or modify Eden Well" not in line]
        self.assertNotRegex("\n".join(other_lines), r"(?i)eden")


if __name__ == "__main__":
    unittest.main()
