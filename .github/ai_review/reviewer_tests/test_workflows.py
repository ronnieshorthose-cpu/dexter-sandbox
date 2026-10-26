"""Static security checks on the workflow files (no YAML library needed)."""

import os
import re
import unittest

WORKFLOWS = os.path.join(os.path.dirname(__file__), "..", "..", "workflows")


def read(name):
    with open(os.path.join(WORKFLOWS, name), "r", encoding="utf-8") as f:
        return f.read()


def run_blocks(text):
    """Yield the shell text of every `run:` step, including multi-line blocks."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", line)
        if not m:
            continue
        indent, rest = len(m.group(1)), m.group(2)
        if rest not in ("|", ">", "|-", ">-"):
            yield rest
            continue
        block = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            block.append(nxt)
        yield "\n".join(block)


def job_section(text, job):
    m = re.search(rf"^  {job}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)", text, re.S | re.M)
    assert m, f"job {job} not found"
    return m.group(1)


ALL = ("ai-review.yml", "ai-review-tests.yml", "ai-review-env-probe.yml")


class AllWorkflowTests(unittest.TestCase):
    def test_no_expressions_inside_shell(self):
        for name in ALL:
            for block in run_blocks(read(name)):
                with self.subTest(workflow=name, block=block[:60]):
                    self.assertNotIn("${{", block)

    def test_actions_pinned_to_full_sha(self):
        for name in ALL:
            for ref in re.findall(r"(?m)^\s*(?:- )?uses:\s*(\S+)", read(name)):
                with self.subTest(workflow=name, uses=ref):
                    self.assertRegex(ref, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")

    def test_checkout_never_targets_pr_code(self):
        for name in ALL:
            text = read(name)
            with self.subTest(workflow=name):
                self.assertNotIn("allow-unsafe-pr-checkout", text)
                self.assertNotRegex(text, r"^\s+ref:", "checkout must use the default ref")
                self.assertNotRegex(text, r"^\s+repository:")
                self.assertNotIn("head.sha", text)
                self.assertNotIn("head_ref", text)
                self.assertNotRegex(text, r"git (fetch|checkout|clone)|gh pr checkout")
                if "actions/checkout" in text:
                    self.assertEqual(text.count("actions/checkout"), text.count("persist-credentials: false"))

    def test_untrusted_pr_text_never_referenced(self):
        for name in ALL:
            text = read(name)
            for field in ("pull_request.title", "pull_request.body", "head.ref", "head.label", "comment.body"):
                with self.subTest(workflow=name, field=field):
                    self.assertNotIn(field, text)

    def test_top_level_permissions_empty_or_read_only(self):
        for name in ALL:
            text = read(name)
            m = re.search(r"^permissions:(.*?)(?=^\S)", text, re.S | re.M)
            with self.subTest(workflow=name):
                self.assertIsNotNone(m)
                self.assertNotIn("write", m.group(1))

    def test_no_comment_or_workflow_run_triggers(self):
        for name in ALL:
            text = read(name)
            for trigger in ("issue_comment", "workflow_run", "pull_request_review"):
                with self.subTest(workflow=name, trigger=trigger):
                    self.assertNotRegex(text, rf"^\s+{trigger}:", re.M)


class ReviewerWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = read("ai-review.yml")

    def test_triggers(self):
        self.assertRegex(self.text, r"(?m)^  pull_request_target:")
        self.assertRegex(self.text, r"(?m)^  workflow_dispatch:")
        self.assertNotRegex(self.text, r"(?m)^  pull_request:")

    def test_both_jobs_gated_on_mode_main_and_same_repo(self):
        for job, mode in (("dry-run", "dry-run"), ("live", "live")):
            section = job_section(self.text, job)
            with self.subTest(job=job):
                self.assertIn(f"vars.AI_REVIEW_MODE == '{mode}'", section)
                self.assertIn("github.ref == 'refs/heads/main'", section)
                self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", section)

    def test_only_live_job_gets_secret_and_environment(self):
        dry, live = job_section(self.text, "dry-run"), job_section(self.text, "live")
        self.assertNotIn("secrets.", dry)
        self.assertNotIn("environment:", dry)
        self.assertIn("environment: ai-review", live)
        self.assertEqual(self.text.count("secrets."), 1)
        self.assertIn("OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}", live)

    def test_job_permissions_minimal(self):
        for job in ("dry-run", "live"):
            section = job_section(self.text, job)
            perms = re.search(r"permissions:\n((?:\s{6}\S.*\n)+)", section).group(1)
            granted = {line.strip() for line in perms.splitlines()}
            with self.subTest(job=job):
                self.assertEqual(granted, {"contents: read", "pull-requests: write", "statuses: write"})

    def test_concurrency_per_pr_with_cancel(self):
        self.assertIn("cancel-in-progress: true", self.text)
        self.assertIn("github.event.pull_request.number || inputs.pr_number", self.text)

    def test_isolated_python_flags(self):
        self.assertEqual(self.text.count("python3 -E -s -m dexter_review"), 2)


class OtherWorkflowTests(unittest.TestCase):
    def test_tests_workflow_is_unprivileged(self):
        text = read("ai-review-tests.yml")
        self.assertNotIn("secrets.", text)
        self.assertNotIn("environment:", text)
        self.assertNotIn("write", text)
        self.assertNotIn("pull_request_target", text)

    def test_probe_never_prints_secret(self):
        text = read("ai-review-env-probe.yml")
        self.assertIn("vars.AI_REVIEW_PROBE == 'enabled'", text)
        self.assertEqual(re.findall(r"secrets\.(\w+)", text), ["AI_REVIEW_CANARY"])
        for block in run_blocks(text):
            self.assertNotRegex(block, r"echo[^\n]*\$CANARY")
        self.assertNotIn("OPENAI_API_KEY", text)


if __name__ == "__main__":
    unittest.main()
