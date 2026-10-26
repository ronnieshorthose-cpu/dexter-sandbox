"""In-memory stand-ins for GitHub and OpenAI. No test touches the network."""

import json

from dexter_review.clients import HTTPError, OpenAIError
from dexter_review.config import PAID_CALLS_APPROVAL_PHRASE, load_config
from dexter_review.render import BOT_LOGIN

REPO = "owner/sandbox"
BASE_SHA = "b" * 40
HEAD_SHA = "a" * 40
NEW_SHA = "c" * 40

SIMPLE_DIFF = (
    "diff --git a/app.py b/app.py\n"
    "--- a/app.py\n+++ b/app.py\n@@ -1 +1,2 @@\n print('hi')\n+print('there')\n"
)

# Fictional prices used only by tests. Real prices must come from OpenAI's pricing page.
LIVE_ENV = {
    "GITHUB_REPOSITORY": REPO,
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_RUN_ID": "42",
    "AI_REVIEW_MODE": "live",
    "AI_REVIEW_MODEL": "gpt-6.1-sol",
    "AI_REVIEW_PRICE_INPUT_USD_PER_MTOK": "2",
    "AI_REVIEW_PRICE_OUTPUT_USD_PER_MTOK": "10",
    "AI_REVIEW_MAX_PAID_CALLS_PER_PR": "3",
    "AI_REVIEW_MAX_COST_PER_RUN_USD": "1.00",
    "AI_REVIEW_PAID_CALLS_APPROVED": PAID_CALLS_APPROVAL_PHRASE,
    "OPENAI_API_KEY": "test-key-not-real",
    "GH_TOKEN": "test-gh-token",
}


def make_cfg(**overrides):
    env = dict(LIVE_ENV)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return load_config(env)


def pr_payload(head_sha=HEAD_SHA, *, state="open", draft=False, head_repo=REPO):
    return {
        "number": 7,
        "state": state,
        "draft": draft,
        "head": {"sha": head_sha, "repo": {"full_name": head_repo}},
        "base": {"sha": BASE_SHA, "repo": {"full_name": REPO}},
    }


def review_json(findings=None, **overrides):
    data = {
        "summary": "Looks reasonable.",
        "overall_risk": "low",
        "findings": findings if findings is not None else [],
        "testing_gaps": [],
        "suspicious_instructions": [],
    }
    data.update(overrides)
    return json.dumps(data)


def openai_response(text=None, status="completed", usage=None):
    return {
        "status": status,
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text if text is not None else review_json()}],
            },
        ],
        "usage": usage or {"input_tokens": 1000, "output_tokens": 500},
    }


class FakeGitHub:
    def __init__(self, pr=None, diff=SIMPLE_DIFF):
        self.pr = pr or pr_payload()
        self.head_after_review = None  # simulate a push while the review runs
        self.diff = diff
        self.diff_error = None
        self.comments = []
        self.statuses = []
        self.calls = []
        self._next_id = 100
        self._get_pull_count = 0

    def get_pull(self, number):
        self.calls.append(("get_pull", number))
        self._get_pull_count += 1
        if self._get_pull_count > 1 and self.head_after_review:
            pr = json.loads(json.dumps(self.pr))
            pr["head"]["sha"] = self.head_after_review
            return pr
        return self.pr

    def get_compare_diff(self, base, head):
        self.calls.append(("diff", base, head))
        if self.diff_error:
            raise self.diff_error
        return self.diff

    def list_issue_comments(self, number):
        return [dict(c) for c in self.comments]

    def add_comment(self, body, login=BOT_LOGIN, user_type="Bot"):
        self._next_id += 1
        self.comments.append({"id": self._next_id, "body": body, "user": {"login": login, "type": user_type}})
        return self._next_id

    def create_comment(self, number, body):
        self.calls.append(("create_comment",))
        cid = self.add_comment(body)
        return {"id": cid}

    def update_comment(self, comment_id, body):
        self.calls.append(("update_comment", comment_id))
        for c in self.comments:
            if c["id"] == comment_id:
                c["body"] = body
                return {"id": comment_id}
        raise HTTPError(404, "not found")

    def create_status(self, sha, state, description, context, target_url=""):
        assert len(description) <= 140, description
        self.statuses.append((sha, state, description))

    def bot_bodies(self):
        return [c["body"] for c in self.comments if c["user"]["login"] == BOT_LOGIN]


class FakeOpenAI:
    """Replays scripted results: dicts are responses, exceptions are raised."""

    def __init__(self, *script, max_attempts=2):
        self.script = list(script) or [openai_response()]
        self.max_attempts = max_attempts
        self.attempts = 0
        self.payloads = []

    def create_response(self, payload):
        self.payloads.append(payload)
        for _ in range(self.max_attempts):
            self.attempts += 1
            item = self.script.pop(0) if self.script else OpenAIError("script exhausted", False)
            if isinstance(item, OpenAIError):
                if item.retryable and self.attempts < self.max_attempts:
                    continue
                raise item
            return item
        raise OpenAIError("unreachable", False)


class FactorySpy:
    """openai_factory that records whether a paid client was ever created."""

    def __init__(self, client=None):
        self.client = client or FakeOpenAI()
        self.created = 0

    def __call__(self):
        self.created += 1
        return self.client
