"""Phase 2D: the reviewer token may write to pull requests; pin down how it is used.

Dry run #3 failed with HTTP 403 on POST /issues/3/comments. The token had
`issues: write` and `pull-requests: read`, but GitHub checks the pull-requests
permission, not the issues one, when a GitHub App token (GITHUB_TOKEN is one)
comments on a pull request through the issues API. Both reviewer jobs now have:

    contents: read, pull-requests: write, issues: read, statuses: write

`pull-requests: write` allows more than commenting. With it a GITHUB_TOKEN can,
among other things:
  - submit pull request reviews (comment, request changes, or approve);
  - open pull requests;
  - edit a pull request's title, body or base branch, and close or reopen it;
  - request or remove reviewers;
  - add, edit or delete comments and review comments on pull requests.

What limits it:
  - GitHub refuses approvals and new pull requests from GITHUB_TOKEN while the
    repository setting "Allow GitHub Actions to create and approve pull
    requests" is off. Only the owner can change it, and it must stay off.
  - Merging needs `contents: write`, which neither job has; the
    "Protect Dexter Main" ruleset also requires a pull request and passing checks.
  - The token is used only by reviewer code from main. PR code is never checked
    out or run, and fork PRs are skipped.
  - The reviewer's GitHub client writes only PR comments (through the issues API)
    and commit statuses. The tests below fail if it gains any other write call.
"""

import os
import re
import unittest

from dexter_review.clients import GitHubClient

from reviewer_tests.fakes import BASE_SHA, HEAD_SHA, REPO
from reviewer_tests.test_workflows import ALL, job_section, read

HERE = os.path.dirname(__file__)
PACKAGE = os.path.join(HERE, "..", "dexter_review")
README = os.path.join(HERE, "..", "README.md")
ASSESSMENT = os.path.join(HERE, "..", "..", "..", "docs", "PHASE-2C-SECURITY-ASSESSMENT.md")

INTENDED = {"contents": "read", "pull-requests": "write", "issues": "read", "statuses": "write"}
APPROVAL_SETTING = "Allow GitHub Actions to create and approve pull requests"


def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def without_comments(text):
    """The workflow without comment lines, which explain these permissions by name."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def job_permissions(text, job):
    block = re.search(r"permissions:\n((?:\s{6}\S.*\n)+)", job_section(text, job)).group(1)
    pairs = [line.strip().split(":", 1) for line in block.splitlines()]
    return {key.strip(): value.strip() for key, value in pairs}


class JobPermissionTests(unittest.TestCase):
    def setUp(self):
        self.text = read("ai-review.yml")
        self.settings = without_comments(self.text)

    def test_reviewer_jobs_have_exactly_the_intended_permissions(self):
        for job in ("dry-run", "live"):
            with self.subTest(job=job):
                self.assertEqual(job_permissions(self.text, job), INTENDED)

    def test_only_pull_requests_and_statuses_are_writable(self):
        for job in ("dry-run", "live"):
            perms = job_permissions(self.text, job)
            with self.subTest(job=job):
                self.assertEqual({k for k, v in perms.items() if v == "write"}, {"pull-requests", "statuses"})

    def test_issues_is_read_only(self):
        # Writing real issues is not needed: PR comments are covered by pull-requests: write.
        self.assertNotIn("issues: write", self.settings)
        self.assertEqual(self.settings.count("issues: read"), 2)

    def test_no_workflow_can_push_or_merge(self):
        # Merging a pull request needs contents: write.
        for name in ALL:
            with self.subTest(workflow=name):
                self.assertNotIn("contents: write", without_comments(read(name)))

    def test_pull_requests_write_only_in_the_two_reviewer_jobs(self):
        self.assertEqual(self.settings.count("pull-requests: write"), 2)
        for name in ALL:
            if name != "ai-review.yml":
                with self.subTest(workflow=name):
                    self.assertNotIn("pull-requests", read(name))

    def test_workflow_explains_the_permission_and_its_limits(self):
        dry = job_section(self.text, "dry-run")
        self.assertIn("403", dry)
        self.assertIn(APPROVAL_SETTING, dry)
        self.assertIn("contents: write", dry)


class RecordingTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append((method, url))
        return (200, b"[]") if method == "GET" else (201, b"{}")


# One call per public GitHubClient method. A new method must be added here,
# which makes its HTTP requests subject to the checks below.
CLIENT_CALLS = {
    "get_pull": (7,),
    "get_compare_diff": (BASE_SHA, HEAD_SHA),
    "list_issue_comments": (7,),
    "list_pull_files": (7,),
    "list_pull_commits": (7,),
    "list_statuses": (HEAD_SHA,),
    "create_comment": (7, "body"),
    "update_comment": (99, "body"),
    "create_status": (HEAD_SHA, "failure", "description", "context"),
}

ALLOWED_WRITES = {
    ("POST", f"/repos/{REPO}/issues/7/comments"),
    ("PATCH", f"/repos/{REPO}/issues/comments/99"),
    ("POST", f"/repos/{REPO}/statuses/{HEAD_SHA}"),
}


class ClientWriteScopeTests(unittest.TestCase):
    def requests(self):
        transport = RecordingTransport()
        gh = GitHubClient(REPO, "t", transport=transport)
        for name, args in CLIENT_CALLS.items():
            getattr(gh, name)(*args)
        return [(m, url.replace("https://api.github.com", "").split("?")[0]) for m, url in transport.requests]

    def test_every_client_method_is_covered(self):
        public = {n for n in dir(GitHubClient) if not n.startswith("_") and callable(getattr(GitHubClient, n))}
        self.assertEqual(public, set(CLIENT_CALLS))

    def test_writes_are_only_comments_and_statuses(self):
        writes = {(m, path) for m, path in self.requests() if m != "GET"}
        self.assertEqual(writes, ALLOWED_WRITES)

    def test_nothing_is_written_to_a_pulls_endpoint(self):
        # pull-requests: write is used only through the issues comments API.
        for method, path in self.requests():
            if "/pulls" in path:
                with self.subTest(path=path):
                    self.assertEqual(method, "GET")

    def test_source_has_no_review_merge_or_pull_request_edit_calls(self):
        forbidden = (
            "/reviews", "/merge", "requested_reviewers", "/labels", "/assignees", "/lock",
            '"PUT"', '"DELETE"',
        )
        for name in sorted(os.listdir(PACKAGE)):
            if not name.endswith(".py"):
                continue
            source = read_file(os.path.join(PACKAGE, name))
            for pattern in forbidden:
                with self.subTest(file=name, pattern=pattern):
                    self.assertNotIn(pattern, source)
            with self.subTest(file=name, pattern="write to /pulls"):
                self.assertNotRegex(source, r'"(?:POST|PATCH|PUT|DELETE)"\s*,\s*f?"[^"]*/pulls')


class DocumentationTests(unittest.TestCase):
    def test_readme_states_permissions_authority_and_limits(self):
        text = read_file(README)
        section = text[text.index("**Permissions.**"):text.index("## Files")]
        for phrase in ("pull-requests: write", "issues: read", "contents: write", APPROVAL_SETTING, "approve"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)
        self.assertNotIn("is enough to comment on a pull request", text)

    def test_activation_keeps_the_approval_setting_off(self):
        text = read_file(README)
        activation = text[text.index("## Activation"):]
        self.assertIn(f'confirm "{APPROVAL_SETTING}" is **off**', activation)

    def test_assessment_records_phase_2d(self):
        text = read_file(ASSESSMENT)
        self.assertIn("## Phase 2D", text)
        self.assertIn("pull-requests: write", text[text.index("## Phase 2D"):])


if __name__ == "__main__":
    unittest.main()
