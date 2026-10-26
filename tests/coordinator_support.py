"""Shared helpers for the coordinator tests. Every test uses a throwaway database."""

import json
import os
import shutil
import tempfile
import unittest

from dexter_coordinator.coordinator import Coordinator
from dexter_coordinator.models import CLAUDE

PASS = "owner-pass-for-tests"
FAST_KDF = 2 ** 10  # scrypt cost for tests only; real databases use auth.DEFAULT_COST


class Clock:
    """A clock that moves one second per reading, so timestamps are predictable."""

    def __init__(self):
        self.tick = 0

    def __call__(self):
        self.tick += 1
        minutes, seconds = divmod(self.tick, 60)
        hours, minutes = divmod(minutes, 60)
        return f"2026-10-26T{hours:02d}:{minutes:02d}:{seconds:02d}Z"


def response_text(handoff, *, fenced=True, prose=True, **overrides):
    """A reply in the dexter-response/v1 format for a handoff dict."""
    body = {
        "format": "dexter-response/v1",
        "correlation_id": handoff["correlation_id"],
        "task_id": handoff["task"],
        "from_role": handoff["to_role"],
        "summary": "Work reported in plain English.",
        "references": ["branch dexter/example"],
    }
    if handoff["action"] == "implement":
        body.update(outcome="done", tests=[{"name": "test_books", "result": "passed", "details": ""}])
    else:
        body.update(outcome="recommend_accept", findings=[])
    for key, value in overrides.items():
        if value is None:
            body.pop(key, None)
        else:
            body[key] = value
    text = json.dumps(body, indent=2)
    if fenced:
        text = "```json\n" + text + "\n```"
    if prose:
        text = "Here is my report.\n\n" + text + "\n\nThanks!"
    return text


class Seconds:
    """Wall-clock seconds that jump an hour per reading, so lockouts never slow tests down.
    Tests of the lockout itself replace `now_seconds` with a fixed clock."""

    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        self.now += 3600
        return self.now


def make_coordinator(path, clock=None):
    return Coordinator(path, clock=clock or Clock(), kdf_cost=FAST_KDF, now_seconds=Seconds())


class CoordinatorTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dexter-test-")
        self.path = os.path.join(self.dir, "dexter.db")
        self.clock = Clock()
        self.c = make_coordinator(self.path, self.clock)
        self.c.set_owner_passphrase(PASS)
        self.project = self.c.create_project("Pocket Library", "A fictional book-lending tracker.")["id"]

    def tearDown(self):
        self.c.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def reopen(self):
        self.c.close()
        self.c = make_coordinator(self.path, self.clock)
        return self.c

    def new_task(self, name="Book list", **kwargs):
        kwargs.setdefault("description", "Show every book in the library.")
        kwargs.setdefault("criteria", ["Lists every book"])
        kwargs.setdefault("assignee", CLAUDE)
        description = kwargs.pop("description")
        return self.c.create_task(kwargs.pop("project", self.project), name, description, **kwargs)["id"]

    # Drive a task forward through the normal workflow, from wherever it is now.
    def advance(self, task, target):
        steps = {
            "BACKLOG": lambda: self.c.plan(task),
            "PLANNED": lambda: self.c.submit_for_approval(task),
            "AWAITING_APPROVAL": lambda: self.c.approve(task, PASS),
            "APPROVED": lambda: self.c.start(task),
            "IN_PROGRESS": lambda: (self.report_done(task), self.c.send_to_review(task)),
            "IN_REVIEW": lambda: (self.review(task), self.c.mark_ready(task)),
            "READY_FOR_HUMAN": lambda: self.c.complete(task, PASS),
        }
        while self.state(task) != target:
            steps[self.state(task)]()
        return task

    def approved(self, task=None, **kwargs):
        return self.advance(task or self.new_task(**kwargs), "APPROVED")

    def in_progress(self, task=None, **kwargs):
        return self.advance(task or self.new_task(**kwargs), "IN_PROGRESS")

    def in_review(self, task=None, **kwargs):
        return self.advance(task or self.new_task(**kwargs), "IN_REVIEW")

    def ready(self, task=None, **kwargs):
        return self.advance(task or self.new_task(**kwargs), "READY_FOR_HUMAN")

    def completed(self, task=None, **kwargs):
        return self.advance(task or self.new_task(**kwargs), "COMPLETED")

    def report_done(self, task, **overrides):
        handoff = self.c.create_handoff(task)
        self.c.record_response(handoff["id"], response_text(handoff, **overrides), PASS)
        return handoff

    def review(self, task, **overrides):
        handoff = self.c.create_handoff(task)
        self.c.record_response(handoff["id"], response_text(handoff, **overrides), PASS)
        return handoff

    def state(self, task):
        return self.c.get_task(task)["state"]
