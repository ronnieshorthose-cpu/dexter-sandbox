"""Task creation, persistence, input validation, roles and dependencies."""

import os
import sqlite3
import unittest

from dexter_coordinator.coordinator import Coordinator
from dexter_coordinator.models import (
    CHATGPT,
    CLAUDE,
    NotFound,
    RuleError,
    ValidationError,
)
from dexter_coordinator.store import StoreError

from tests.coordinator_support import PASS, CoordinatorTestCase


class CreationAndPersistenceTests(CoordinatorTestCase):
    def test_ids_are_unique_and_sequential(self):
        first, second = self.new_task("One"), self.new_task("Two")
        self.assertEqual((first, second), ("TASK-0001", "TASK-0002"))
        other = self.c.create_project("Second project")["id"]
        self.assertEqual(self.new_task("Three", project=other), "TASK-0003")
        self.assertEqual(other, "PRJ-002")

    def test_every_field_is_stored(self):
        dep = self.new_task("Book model")
        task = self.c.create_task(
            self.project, "Book list", "Show every book.\nSorted by title.", priority="high",
            criteria=["Lists every book", "Sorted by title"], depends_on=[dep], assignee=CHATGPT,
        )
        self.assertEqual(task["id"], "TASK-0002")
        self.assertEqual(task["project"], self.project)
        self.assertEqual(task["name"], "Book list")
        self.assertEqual(task["description"], "Show every book.\nSorted by title.")
        self.assertEqual(task["priority"], "high")
        self.assertEqual(task["assignee"], CHATGPT)
        self.assertEqual(task["criteria"], ["Lists every book", "Sorted by title"])
        self.assertEqual(task["depends_on"], [{"id": dep, "name": "Book model", "state": "BACKLOG"}])
        self.assertEqual(task["state"], "BACKLOG")
        self.assertEqual(task["approval"], {"status": "not_requested"})
        self.assertEqual(task["test_results"], [])
        self.assertEqual(task["review_findings"], [])
        self.assertRegex(task["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(self.c.get_task(dep)["dependents"], ["TASK-0002"])

    def test_tasks_and_history_survive_reopening(self):
        task = self.in_progress()
        before = self.c.get_task(task)
        history = self.c.history(task_key=task)
        self.reopen()
        self.assertEqual(self.c.get_task(task), before)
        self.assertEqual(self.c.history(task_key=task), history)
        self.assertEqual(self.c.verify(), [])

    def test_history_records_actor_and_time_for_every_change(self):
        task = self.approved()
        events = self.c.history(task_key=task)
        self.assertEqual([e["action"] for e in events], ["task_created", "state_changed", "state_changed", "state_changed"])
        self.assertEqual([e["actor"] for e in events], ["dexter", "dexter", "dexter", "human_owner"])
        for e in events:
            self.assertRegex(e["ts"], r"Z$")
        self.assertIn("SIMULATED", events[-1]["details"]["label"])

    def test_list_tasks_filters(self):
        a = self.new_task("A")
        self.approved(self.new_task("B"))
        self.assertEqual([t["id"] for t in self.c.list_tasks(self.project, "backlog")], [a])
        self.assertEqual(len(self.c.list_tasks()), 2)
        with self.assertRaises(ValidationError):
            self.c.list_tasks(state="FINISHED")


class MalformedInputTests(CoordinatorTestCase):
    def test_bad_task_fields_are_rejected(self):
        cases = {
            "empty name": dict(name="  "),
            "long name": dict(name="x" * 121),
            "escape sequence": dict(name="Book \x1b[31mlist"),
            "direction override": dict(name="Book \u202elist"),
            "newline in name": dict(name="Book\nlist"),
            "empty description": dict(description=""),
            "bad priority": dict(priority="urgent"),
            "criteria not a list": dict(criteria="Lists books"),
            "criterion not text": dict(criteria=[42]),
            "empty criterion": dict(criteria=[" "]),
            "too many criteria": dict(criteria=[f"c{i}" for i in range(31)]),
        }
        for label, override in cases.items():
            kwargs = {"name": "Book list", "description": "Show books.", "criteria": ["Lists books"]}
            kwargs.update(override)
            with self.subTest(label):
                with self.assertRaises(ValidationError):
                    self.c.create_task(self.project, kwargs.pop("name"), kwargs.pop("description"), **kwargs)
        self.assertEqual(self.c.list_tasks(), [])

    def test_bad_ids_are_rejected(self):
        for bad in ("TASK-1", "task-0001; DROP TABLE tasks", "../TASK-0001", "", None, 7):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.c.get_task(bad)
        with self.assertRaises(NotFound):
            self.c.get_task("TASK-9999")
        with self.assertRaises(NotFound):
            self.c.create_task("PRJ-999", "x", "y")
        self.assertEqual(self.c.get_task(self.new_task())["id"], "TASK-0001")
        self.assertEqual(self.c.get_task("task-0001")["id"], "TASK-0001")

    def test_duplicate_project_names_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.c.create_project("pocket library")

    def test_a_file_that_is_not_a_dexter_database_is_refused(self):
        junk = os.path.join(self.dir, "junk.db")
        with open(junk, "wb") as f:
            f.write(b"this is not sqlite" * 100)
        with self.assertRaises(StoreError):
            Coordinator(junk)
        other = os.path.join(self.dir, "other.db")
        conn = sqlite3.connect(other)
        conn.execute("CREATE TABLE something (x)")
        conn.commit()
        conn.close()
        with self.assertRaises(StoreError):
            Coordinator(other)

    def test_failed_change_leaves_nothing_behind(self):
        events = len(self.c.history())
        with self.assertRaises(NotFound):
            self.c.create_task(self.project, "Loans", "Track loans", depends_on=["TASK-0042"])
        self.assertEqual(self.c.list_tasks(), [])
        self.assertEqual(len(self.c.history()), events)


class RoleAssignmentTests(CoordinatorTestCase):
    def test_only_ai_roles_can_be_assigned(self):
        task = self.new_task(assignee=None)
        for role in ("human_owner", "dexter", "gpt", "", None):
            with self.subTest(role=role), self.assertRaises(ValidationError):
                self.c.assign(task, role)
        self.assertEqual(self.c.assign(task, "claude")["assignee"], CLAUDE)
        self.assertEqual(self.c.assign(task, CHATGPT)["assignee"], CHATGPT)

    def test_planning_needs_an_assignee_and_criteria(self):
        task = self.new_task(assignee=None, criteria=[])
        with self.assertRaisesRegex(RuleError, "assigned AI role.*acceptance criterion"):
            self.c.plan(task)
        self.c.assign(task, CLAUDE)
        self.c.add_criterion(task, "Lists every book")
        self.assertEqual(self.c.plan(task)["state"], "PLANNED")

    def test_content_is_locked_once_submitted(self):
        task = self.new_task()
        other = self.new_task("Other")
        self.c.plan(task)
        self.c.submit_for_approval(task)
        changes = (
            lambda: self.c.edit_task(task, name="Renamed"),
            lambda: self.c.add_criterion(task, "More"),
            lambda: self.c.assign(task, CHATGPT),
            lambda: self.c.add_dependency(task, other),
        )
        for change in changes:
            with self.assertRaisesRegex(RuleError, "Withdraw"):
                change()
        self.c.withdraw(task)
        self.assertEqual(self.c.edit_task(task, name="Renamed")["name"], "Renamed")
        self.c.submit_for_approval(task)
        self.c.approve(task, PASS)
        with self.assertRaisesRegex(RuleError, "BACKLOG or PLANNED"):
            self.c.assign(task, CHATGPT)


class DependencyTests(CoordinatorTestCase):
    def test_dependency_rules(self):
        a, b, c = self.new_task("A"), self.new_task("B"), self.new_task("C")
        self.c.add_dependency(b, a)
        self.c.add_dependency(c, b)
        with self.assertRaisesRegex(RuleError, "itself"):
            self.c.add_dependency(a, a)
        with self.assertRaisesRegex(RuleError, "loop"):
            self.c.add_dependency(a, b)
        with self.assertRaisesRegex(RuleError, "loop"):
            self.c.add_dependency(a, c)
        with self.assertRaisesRegex(RuleError, "already depends"):
            self.c.add_dependency(b, a)
        other_project = self.c.create_project("Other")["id"]
        elsewhere = self.new_task("Elsewhere", project=other_project)
        with self.assertRaisesRegex(RuleError, "different project"):
            self.c.add_dependency(a, elsewhere)
        with self.assertRaisesRegex(RuleError, "different project"):
            self.new_task("D", depends_on=[elsewhere])
        self.c.remove_dependency(c, b)
        self.assertEqual(self.c.get_task(c)["depends_on"], [])
        with self.assertRaisesRegex(RuleError, "does not depend"):
            self.c.remove_dependency(c, b)

    def test_work_waits_for_dependencies_to_be_completed(self):
        first = self.new_task("Book model")
        second = self.approved(self.new_task("Book list", depends_on=[first]))
        with self.assertRaisesRegex(RuleError, f"waiting for its dependencies.*{first}"):
            self.c.start(second)
        self.approved(first)
        with self.assertRaisesRegex(RuleError, "waiting"):
            self.c.start(second)  # approved is not enough; it must be COMPLETED
        self.completed(first)
        self.assertEqual(self.c.start(second)["state"], "IN_PROGRESS")

    def test_cancelled_dependency_is_explained(self):
        first = self.new_task("Book model")
        second = self.approved(self.new_task("Book list", depends_on=[first]))
        self.c.cancel(first, PASS, "Not needed")
        with self.assertRaisesRegex(RuleError, "cancelled or rejected"):
            self.c.start(second)


if __name__ == "__main__":
    unittest.main()
