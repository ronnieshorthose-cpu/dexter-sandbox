"""The guided menu: python -m dexter_coordinator start (or double-click dexter.bat on Windows).

A beginner-friendly front end over the same Coordinator the command line uses.
It adds no new rules and no new way to act as the owner:

  - every step calls the same Coordinator method as the matching command, so
    the engine's checks (states, dependencies, evidence, independent review,
    signatures) apply unchanged;
  - every owner action shows the same SIMULATED-approval banner and asks for
    the passphrase again through cli.read_passphrase (interactive terminal
    only). The passphrase is never stored, remembered or written to a file;
  - an empty passphrase cancels the step instead of counting as a wrong one;
  - every AI reply is shown under the UNVERIFIED AI RESPONSE banner.

Handoff text and pasted replies live in private folders next to the database
(see exchange.py), never inside the repository. Nothing here uses the network.
"""

import re
import sqlite3
import sys

from . import cli, dashboard
from .coordinator import utc_now
from .exchange import Exchange, copy_to_clipboard, open_in_browser, open_text_file
from .handoff import MAX_RESPONSE_CHARS
from .labels import FINAL_CHECK_REMINDER, SIMULATED_NOTICE, UNVERIFIED, reply_label, unverified_banner
from .models import (
    ACTIONS,
    APPROVED,
    ARCHITECTURE_REVIEW,
    AWAITING_APPROVAL,
    BACKLOG,
    BLOCKED,
    CANCELLED,
    CHATGPT,
    CLAUDE,
    COMPLETED,
    IMPLEMENT,
    IN_PROGRESS,
    IN_REVIEW,
    PLANNED,
    PRIORITIES,
    READY_FOR_HUMAN,
    REJECTED,
    REVIEW,
    STATE_DESCRIPTIONS,
    TERMINAL_STATES,
    DexterError,
    ValidationError,
    role_title,
    safe_display,
)

say = cli.say

# Where each assistant lives. The owner opens these pages; Dexter never does.
WHERE_TO_PASTE = {
    CHATGPT: "Open ChatGPT (https://chatgpt.com), start a new chat, paste the whole message and send it.",
    CLAUDE: ("Open Claude Code (https://claude.ai/code), start a session on the repository "
             "ronnieshorthose-cpu/dexter-sandbox, paste the whole message and send it."),
}

STATE_HELP = {
    BACKLOG: "The task is written down. It needs someone assigned and at least one acceptance criterion; "
             "then it goes to you for approval.",
    PLANNED: "The task is planned. Send it to yourself (the owner) for approval.",
    AWAITING_APPROVAL: "Waiting for your decision. You can ask ChatGPT to review the plan first.",
    APPROVED: "You approved it. Work can start once every task it depends on is completed.",
    IN_PROGRESS: "The assigned AI is doing the work. Dexter prepares the instructions; you copy them over and "
                 "paste the reply back.",
    IN_REVIEW: "The other AI reviews the work independently. The AI that did the work cannot review itself.",
    READY_FOR_HUMAN: "An independent review recommends acceptance. Check the work on GitHub yourself, then accept "
                     "it or send it back.",
    BLOCKED: "Stopped until the blocker is cleared.",
    COMPLETED: "Finished: you accepted it (SIMULATED approval in Dexter only).",
    REJECTED: "Finished: you rejected it.",
    CANCELLED: "Finished: you cancelled it.",
}

# Most urgent first: the owner's decisions, then work in flight, then new work.
URGENCY = (READY_FOR_HUMAN, AWAITING_APPROVAL, IN_REVIEW, IN_PROGRESS, APPROVED, PLANNED, BACKLOG, BLOCKED)

HOME_OPTIONS = (
    ("N", "Do the next step"),
    ("P", "Projects"),
    ("T", "Tasks"),
    ("H", "Handoffs (copy to an AI / paste a reply)"),
    ("R", "Reports and history"),
    ("D", "Open the read-only dashboard in your browser"),
    ("C", "Check records and back up"),
    ("?", "Help"),
    ("Q", "Quit"),
)

HELP_TEXT = """
HOW DEXTER WORKS
  You (the owner) decide. Dexter keeps the records and prepares messages.
  ChatGPT plans and reviews. Claude Code writes the code and runs the tests.
  The two assistants never talk to Dexter or to each other: you copy each
  message to them and paste their reply back.

A TASK'S JOURNEY
  1. Create it: what to build, who builds it, how you will know it is done.
  2. Approve it (owner passphrase). Optionally ask ChatGPT to review it first.
  3. Dexter prepares instructions for Claude Code. Copy them over; paste the reply back.
  4. Dexter prepares a review request for ChatGPT, the independent reviewer.
  5. If the review recommends acceptance, check the work on GitHub yourself,
     then accept it in Dexter (owner passphrase).

SAFETY
  - Every AI reply is UNVERIFIED. Dexter cannot prove who wrote it, and test
    results are only what the reply says.
  - Owner approvals are SIMULATED: a local passphrase, not GitHub. Accepting a
    task in Dexter does not merge, deploy or approve anything on GitHub.
  - Never give your passphrase to ChatGPT, Claude Code or any other AI tool.
  - Dexter keeps its database, handoff files, replies and backups in a private
    folder outside the repository.

MOVING AROUND
  Type the letter or number of a choice and press Enter. B goes back. To
  cancel an owner action, press Enter without typing a passphrase.
"""


class Back(Exception):
    """Return to the previous menu."""


class Quit(Exception):
    """Leave the guided menu."""


def stamp(now):
    """A timestamp usable in file names on every system (no colons)."""
    return re.sub(r"[^0-9T]", "", now)


class Guide:
    def __init__(self, coord, *, ask=input, passphrase=None, platform=sys.platform, clock=utc_now):
        self.c = coord
        self._ask = ask
        # Always the interactive-terminal prompt unless a test injects another.
        self._passphrase = passphrase or cli.read_passphrase
        self.ex = Exchange(coord.store.path, allow_inside_git=coord.allow_inside_git)
        self.windows = platform == "win32"
        self.linux = platform.startswith("linux")
        self.clock = clock
        self.project = None

    # -- input helpers ---------------------------------------------------------------
    def ask(self, prompt):
        try:
            return self._ask(safe_display(prompt))
        except EOFError:
            raise Quit from None

    def choose(self, title, options, *, back=True, default=None):
        """Show numbered or lettered options; return the chosen key."""
        keys = {key.upper(): key for key, _ in options}
        while True:
            say("")
            say(title)
            for key, label in options:
                say(f"  [{key}] {label}")
            if back:
                say("  [B] Back")
            hint = f" (Enter = {default})" if default is not None else ""
            answer = self.ask(f"Choose{hint}: ").strip().upper()
            if not answer and default is not None:
                return default
            if back and answer == "B":
                raise Back
            if answer in keys:
                return keys[answer]
            say(f"Please type one of: {', '.join(k for k, _ in options)}{' or B' if back else ''}.")

    def pick(self, title, items, describe):
        """Choose one item from a list. Returns the item."""
        if not items:
            raise Back
        options = [(str(i), describe(item)) for i, item in enumerate(items, 1)]
        return items[int(self.choose(title, options)) - 1]

    def text(self, prompt, *, required=True):
        while True:
            answer = self.ask(prompt).strip()
            if answer or not required:
                return answer
            if self.confirm("Nothing typed. Go back?", default=True):
                raise Back

    def lines(self, prompt, *, required=False):
        """Several lines of text, finished with an empty line."""
        say(prompt)
        say("(Type one line at a time. Press Enter on an empty line to finish.)")
        result = []
        while True:
            line = self.ask("> ").rstrip()
            if not line.strip():
                if result or not required:
                    return result
                if self.confirm("Nothing typed yet. Go back?", default=True):
                    raise Back
                continue
            result.append(line)

    def confirm(self, prompt, *, default=False):
        hint = "Y/n" if default else "y/N"
        while True:
            answer = self.ask(f"{prompt} ({hint}): ").strip().lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False

    def pause(self):
        self.ask("Press Enter to continue...")

    def owner_passphrase(self):
        """Ask for the passphrase for ONE owner action. Empty input cancels; nothing is remembered."""
        say("Type the owner passphrase (it is not shown), or press Enter without typing to cancel.")
        passphrase = self._passphrase("Owner passphrase: ")
        if not passphrase:
            say("Cancelled. Nothing was changed.")
            raise Back
        return passphrase

    # -- the main loop -----------------------------------------------------------------
    def run(self):
        say("")
        say("Welcome to Dexter, your local project coordinator.")
        say(SIMULATED_NOTICE)
        try:
            if not self.c.owner_is_set():
                self.first_run()
            for warning in self.c.permission_warnings():
                say(f"Warning: {warning}")
            while True:
                self.guarded(self.home)
        except Quit:
            say("")
            say("Goodbye. Everything you did is saved. Start Dexter again whenever you like.")
            return 0

    def guarded(self, step, *args):
        """Run one step; turn problems into plain messages and return to the menu."""
        try:
            step(*args)
        except Back:
            pass
        except Quit:
            raise
        except KeyboardInterrupt:
            say("")
            say("Cancelled. Each change is saved completely or not at all.")
        except DexterError as exc:
            say("")
            say(f"Problem: {exc}")
            say("That step was not carried out. Earlier steps stay saved: each change is saved completely or not "
                "at all. (A wrong passphrase is recorded in the history.)")
        except sqlite3.OperationalError as exc:
            say("")
            if "locked" in str(exc) or "busy" in str(exc):
                say("Problem: the database is busy (another Dexter window is using it). Nothing was changed; try again.")
            else:
                say(f"Problem: database error ({exc}). Nothing was changed. Choose [C] to check the records.")

    def first_run(self):
        say("")
        say("FIRST-TIME SETUP")
        say(f"Dexter will keep its records in {self.c.store.path}")
        say("That folder is outside the repository, so your records and approvals are never committed to GitHub.")
        say("")
        say("Choose an owner passphrase of at least 12 characters, for example four random words.")
        say("You will type it for every owner decision. Whoever knows it is the owner as far as Dexter can tell,")
        say("so never give it to ChatGPT, Claude Code or any other AI tool, and do not save it in a file.")
        say("There is no way to recover a forgotten passphrase.")
        first = self._passphrase("New owner passphrase: ")
        if not first:
            say("No passphrase typed. Dexter needs one before it can be used.")
            raise Quit
        second = self._passphrase("Type it again: ")
        if first != second:
            say("The two passphrases do not match; nothing was saved. Start Dexter again to retry.")
            raise Quit
        try:
            self.c.set_owner_passphrase(first)
        except DexterError as exc:
            say(f"Problem: {exc}. Nothing was saved. Start Dexter again to retry.")
            raise Quit from None
        say("Done. Dexter is set up.")

    def home(self):
        data = dashboard.build(self.c)
        say("")
        say(dashboard.render_text(data, self.c.store.path))
        choice = self.choose("What would you like to do?", HOME_OPTIONS, back=False)
        if choice == "Q":
            raise Quit
        actions = {"N": self.next_step, "P": self.projects_menu, "T": self.tasks_menu, "H": self.handoffs_menu,
                   "R": self.reports_menu, "D": self.open_dashboard, "C": self.check_menu, "?": self.help}
        actions[choice]()

    def help(self):
        say(HELP_TEXT.rstrip())
        self.pause()

    # -- projects ----------------------------------------------------------------------
    def current_project(self):
        projects = self.c.list_projects()
        if not projects:
            say("")
            say("There are no projects yet. Let's create one.")
            return self.create_project()
        ids = [p["id"] for p in projects]
        if self.project not in ids:
            self.project = ids[0] if len(ids) == 1 else self.pick(
                "Which project?", projects, lambda p: f"{p['id']}  {p['name']}  [{p['status']}]")["id"]
        return self.c.get_project(self.project)

    def create_project(self):
        name = self.text("Project name (for example: Pocket Library): ")
        description = self.text("One sentence about what it is for (optional): ", required=False)
        project = self.c.create_project(name, description)
        self.project = project["id"]
        say(f"Created project {project['id']} \"{project['name']}\".")
        return project

    def projects_menu(self):
        while True:
            options = [("1", "Show the current project's tasks"), ("2", "Create a new project"),
                       ("3", "Switch to another project"), ("4", "Pause the project (stops all work; no passphrase)"),
                       ("5", "Resume a paused project (owner passphrase)")]
            choice = self.choose("PROJECTS", options)
            if choice == "1":
                self.guarded(self.show_project)
            elif choice == "2":
                self.guarded(self.create_project)
            elif choice == "3":
                self.project = None
                self.guarded(self.current_project)
            elif choice == "4":
                self.guarded(self.pause_project)
            else:
                self.guarded(self.resume_project)

    def show_project(self):
        project = self.current_project()
        say("")
        say(f"{project['id']}  {project['name']}  [{project['status']}]")
        if project["description"]:
            say(f"  {project['description']}")
        tasks = self.c.list_tasks(project["id"])
        if not tasks:
            say("  No tasks yet.")
        for t in tasks:
            who = role_title(t["assignee"]) if t["assignee"] else "unassigned"
            say(f"  {t['id']}  {t['state']:<18} {t['priority']:<8} {who:<34} {t['name']}")
        self.pause()

    def pause_project(self):
        project = self.current_project()
        reason = self.text(f"Why pause {project['id']}? ")
        self.c.pause_project(project["id"], reason)
        say(f"Paused {project['id']}. Dexter will not move tasks or prepare handoffs until you resume it.")

    def resume_project(self):
        project = self.current_project()
        if project["status"] != "PAUSED":
            say(f"{project['id']} is not paused.")
            return
        cli.human_banner(f"resume project {project['id']} \"{project['name']}\"")
        self.c.resume_project(project["id"], self.owner_passphrase())
        say(f"Resumed {project['id']}.")

    # -- tasks -------------------------------------------------------------------------
    def tasks_menu(self):
        while True:
            choice = self.choose("TASKS", [("1", "Create a new task"), ("2", "Open a task (see its options)"),
                                           ("3", "List every task in the project")])
            if choice == "1":
                self.guarded(self.create_task)
            elif choice == "2":
                self.guarded(self.open_task)
            else:
                self.guarded(self.show_project)

    def describe_task(self, t):
        who = role_title(t["assignee"]).split(" (")[0] if t["assignee"] else "unassigned"
        return f"{t['id']}  {t['name']}  [{t['state']}, {who}]"

    def open_task(self):
        project = self.current_project()
        tasks = self.c.list_tasks(project["id"])
        if not tasks:
            say("No tasks yet. Choose [T] Tasks > Create a new task.")
            return
        self.task_actions(self.pick("Which task?", tasks, self.describe_task)["id"])

    def create_task(self):
        project = self.current_project()
        say("")
        say(f"NEW TASK in {project['id']} {project['name']}")
        name = self.text("Short name (for example: Book catalogue): ")
        description = "\n".join(self.lines("What should be built? Describe it in plain English.", required=True))
        who = self.choose("Who should do the work?", [
            ("1", "Claude Code - writes the code and runs the tests (usual choice)"),
            ("2", "ChatGPT - plans, designs or writes documents")], default="1")
        assignee = CLAUDE if who == "1" else CHATGPT
        criteria = self.lines("How will you know it is done? Give one acceptance criterion per line, "
                              "for example: Books can be added and listed.", required=True)
        priority = self.choose("Priority?", [(str(i), p) for i, p in enumerate(PRIORITIES, 1)], default="3")
        others = [t for t in self.c.list_tasks(project["id"]) if t["state"] not in (REJECTED, CANCELLED)]
        depends = []
        if others:
            say("")
            say("Does this task need other tasks finished first? Type their numbers separated by commas,")
            say("or press Enter for none.")
            for i, t in enumerate(others, 1):
                say(f"  [{i}] {self.describe_task(t)}")
            while True:
                answer = self.ask("Needs first: ").replace(" ", "")
                try:
                    numbers = [int(n) for n in answer.split(",") if n]
                except ValueError:
                    numbers = [0]
                if all(1 <= n <= len(others) for n in numbers):
                    depends = [others[n - 1]["id"] for n in numbers]
                    break
                say("Please type numbers from the list, like 1,2, or press Enter for none.")
        task = self.c.create_task(project["id"], name, description, priority=PRIORITIES[int(priority) - 1],
                                  criteria=criteria, depends_on=depends, assignee=assignee)
        say(f"Created {task['id']} \"{task['name']}\".")
        if self.confirm("Put it in your approval queue now? (Dexter plans it and asks you to approve it)",
                        default=True):
            self.c.plan(task["id"])
            self.c.submit_for_approval(task["id"])
            say(f"{task['id']} is waiting for your approval.")
        self.task_actions(task["id"])

    def next_step(self):
        project = self.current_project()
        tasks = [t for t in self.c.list_tasks(project["id"]) if t["state"] not in TERMINAL_STATES]
        if not tasks:
            say("")
            say(f"{project['id']} has no open tasks.")
            if self.confirm("Create a task now?", default=True):
                self.create_task()
            return
        tasks.sort(key=lambda t: (URGENCY.index(t["state"]), PRIORITIES.index(t["priority"]), t["id"]))
        task = self.pick("Which task? (most urgent first)", tasks,
                         lambda t: f"{self.describe_task(t)}: {STATE_HELP[t['state']].split('.')[0]}")
        self.task_actions(task["id"])

    def show_card(self, task, handoffs):
        say("")
        say("-" * 78)
        say(f"{task['id']}  \"{task['name']}\"   [{task['state']}] {STATE_DESCRIPTIONS[task['state']]}")
        say(f"  Project:     {task['project']} {task['project_name']}"
            + ("  [PAUSED]" if task["project_status"] == "PAUSED" else ""))
        say(f"  Assigned to: {role_title(task['assignee']) if task['assignee'] else '(nobody yet)'}"
            f"    Priority: {task['priority']}")
        deps = ", ".join(f"{d['id']} ({d['state']})" for d in task["depends_on"]) or "(none)"
        say(f"  Needs first: {deps}")
        say(f"  Approval:    {cli.describe_approval(task['approval'])}")
        if task["state"] == BLOCKED:
            say(f"  Blocked:     {task['blocked_reason']}")
        say("  What to build:")
        for line in task["description"].splitlines():
            say(f"    {line}")
        say("  Done when:")
        for i, criterion in enumerate(task["criteria"], 1):
            say(f"    {i}. {criterion}")
        if not task["criteria"]:
            say("    (no acceptance criteria yet)")
        replied = [h for h in handoffs if h["response"] is not None]
        if replied:
            say("  AI replies (newest last):")
            for h in replied[-4:]:
                r = h["response"]
                outcome = r["outcome"] or "unstructured"
                say(f"    {h['id']} {h['action']}: outcome {outcome}")
                say(f"      {reply_label(h, task, handoffs)}")
                if r["structured"] and r["data"].get("tests"):
                    results = [t["result"] for t in r["data"]["tests"]]
                    counted = ", ".join(f"{results.count(x)} {x}" for x in dict.fromkeys(results))
                    say(f"      Tests the reply CLAIMS: {counted} (Dexter did not run them)")
                for f in (r["data"].get("findings") or [])[:5] if r["structured"] else []:
                    say(f"      Finding [{f['severity']}] {f['title']}")
        say(f"  Now: {STATE_HELP[task['state']]}")
        say("-" * 78)

    def task_actions(self, task_id):
        """Show a task and offer the steps that make sense now, the recommended one first."""
        while True:
            task = self.c.get_task(task_id)
            handoffs = self.c.list_handoffs(task_id)
            self.show_card(task, handoffs)
            actions = self.actions_for(task, handoffs)
            if not actions:
                say("This task is finished; there is nothing more to do with it.")
                self.pause()
                return
            options = [(str(i), label + (" (recommended)" if i == 1 and recommended else ""))
                       for i, (label, _, recommended) in enumerate(actions, 1)]
            choice = self.choose(f"What next for {task['id']}?", options)
            self.guarded(actions[int(choice) - 1][1])

    def actions_for(self, task, handoffs):
        """(label, function, recommended) for each sensible step. The engine still checks every rule."""
        tid, state = task["id"], task["state"]
        if state in TERMINAL_STATES:
            return []
        acts = []

        def add(label, fn, recommended=False):
            acts.append((label, fn, recommended))

        current = [h for h in handoffs if self.c.handoff_is_current(h["id"])]
        pending = [h for h in current if h["response"] is None]
        replies = [h for h in current if h["response"] is not None and h["response"]["structured"]]

        def latest(action):
            matching = [h for h in replies if h["action"] == action]
            return matching[-1]["response"]["outcome"] if matching else None

        if task["project_status"] == "PAUSED":
            add("Resume the project (owner passphrase)", self.resume_project, True)
        for h in pending:
            add(f"Paste the reply to {h['id']} from {role_title(h['to_role']).split(' (')[0]}",
                lambda h=h: self.paste_reply(h["id"]), True)
            add(f"Copy {h['id']} again (clipboard, Notepad or screen)", lambda h=h: self.send_handoff(h["id"]))

        if state == BACKLOG:
            if not task["assignee"]:
                add("Choose who does the work", lambda: self.assign(tid), True)
            if not task["criteria"]:
                add("Add acceptance criteria", lambda: self.add_criteria(tid), True)
            if task["assignee"] and task["criteria"]:
                add("Put it in your approval queue", lambda: self.plan_and_submit(tid), True)
            add("Ask ChatGPT to review the plan first", lambda: self.prepare_handoff(tid, ARCHITECTURE_REVIEW))
            add("Edit the name, description or priority", lambda: self.edit_task(tid))
            if task["criteria"]:
                add("Add more acceptance criteria", lambda: self.add_criteria(tid))
        elif state == PLANNED:
            add("Put it in your approval queue", lambda: self.dexter_step("submit_for_approval", tid), True)
            add("Ask ChatGPT to review the plan first", lambda: self.prepare_handoff(tid, ARCHITECTURE_REVIEW))
            add("Move it back to the backlog to edit it", lambda: self.dexter_step("unplan", tid))
        elif state == AWAITING_APPROVAL:
            add("Approve it (owner passphrase)", lambda: self.owner_decision(tid, APPROVED), not pending)
            add("Reject it (owner passphrase)", lambda: self.owner_decision(tid, REJECTED))
            add("Ask ChatGPT to review the plan first", lambda: self.prepare_handoff(tid, ARCHITECTURE_REVIEW))
            add("Withdraw it to make changes", lambda: self.dexter_step("withdraw", tid))
        elif state == APPROVED:
            waiting = [d["id"] for d in task["depends_on"] if d["state"] != COMPLETED]
            if waiting:
                say(f"{tid} cannot start until {', '.join(waiting)} {'is' if len(waiting) == 1 else 'are'} completed.")
            else:
                add(f"Start the work and prepare instructions for {role_title(task['assignee']).split(' (')[0]}",
                    lambda: self.start_work(tid), True)
        elif state == IN_PROGRESS:
            outcome = latest(IMPLEMENT)
            if outcome == "done":
                add("Send the work for an independent review", lambda: self.dexter_step("send_to_review", tid), True)
            elif not pending:
                add(f"Prepare instructions for {role_title(task['assignee']).split(' (')[0]}",
                    lambda: self.prepare_handoff(tid, IMPLEMENT), outcome is None)
            if outcome in ("blocked", "needs_clarification"):
                add("Answer the AI with a new handoff (add your notes)", lambda: self.prepare_handoff(tid, IMPLEMENT),
                    True)
        elif state == IN_REVIEW:
            outcome = latest(REVIEW)
            other = role_title(next(r for r in (CHATGPT, CLAUDE) if r != task["assignee"])).split(" (")[0]
            if outcome == "recommend_accept":
                add("Mark it ready for your final decision", lambda: self.dexter_step("mark_ready", tid), True)
            if outcome in ("recommend_changes", "recommend_accept"):
                add("Send it back for changes", lambda: self.request_changes(tid), outcome == "recommend_changes")
            if outcome is None and not pending:
                add(f"Prepare the independent review request for {other}",
                    lambda: self.prepare_handoff(tid, REVIEW), True)
        elif state == READY_FOR_HUMAN:
            add("Accept the work (owner passphrase)", lambda: self.owner_decision(tid, COMPLETED), True)
            add("Send it back for changes (owner passphrase)", lambda: self.owner_decision(tid, IN_PROGRESS))
        elif state == BLOCKED:
            add("Unblock it (the problem is solved)", lambda: self.dexter_step("unblock", tid), True)

        if state != BLOCKED:
            add("Mark it as blocked", lambda: self.block(tid))
        add("Cancel the task (owner passphrase)", lambda: self.owner_decision(tid, CANCELLED))
        add("Show its history", lambda: self.show_history(task_key=tid))
        # The recommended step goes first; there is at most one.
        first = next((a for a in acts if a[2]), None)
        rest = [(label, fn, False) for label, fn, _ in acts if (label, fn, True) != first]
        return ([first] if first else []) + rest

    # -- task steps --------------------------------------------------------------------
    def dexter_step(self, method, tid):
        getattr(self.c, method)(tid)
        task = self.c.get_task(tid)
        say(f"Done: {tid} is now {task['state']}.")

    def plan_and_submit(self, tid):
        self.c.plan(tid)
        self.c.submit_for_approval(tid)
        say(f"{tid} is waiting for your approval.")

    def assign(self, tid):
        who = self.choose("Who should do the work?", [("1", "Claude Code"), ("2", "ChatGPT")], default="1")
        self.c.assign(tid, CLAUDE if who == "1" else CHATGPT)

    def add_criteria(self, tid):
        for criterion in self.lines("Acceptance criteria, one per line:", required=True):
            self.c.add_criterion(tid, criterion)

    def edit_task(self, tid):
        task = self.c.get_task(tid)
        say("Press Enter to keep the current value.")
        name = self.text(f"Name [{task['name']}]: ", required=False) or None
        description = "\n".join(self.lines("New description (Enter on the first line keeps the current one):")) or None
        priority = self.choose("Priority?", [(str(i), p) for i, p in enumerate(PRIORITIES, 1)],
                               default=str(PRIORITIES.index(task["priority"]) + 1))
        self.c.edit_task(tid, name=name, description=description, priority=PRIORITIES[int(priority) - 1])
        say(f"Updated {tid}.")

    def block(self, tid):
        self.c.block(tid, self.text("What is the blocker? "))
        say(f"{tid} is BLOCKED.")

    def request_changes(self, tid):
        say("Say what needs to change. The AI that did the work sees this in its next instructions.")
        reason = "\n".join(self.lines("Changes needed:", required=True))
        self.c.request_changes(tid, reason)
        say(f"{tid} is back IN_PROGRESS.")
        if self.confirm("Prepare the new instructions now?", default=True):
            self.prepare_handoff(tid, IMPLEMENT)

    def start_work(self, tid):
        self.c.start(tid)
        say(f"{tid} is IN_PROGRESS.")
        self.prepare_handoff(tid, IMPLEMENT)

    def owner_decision(self, tid, to_state):
        task = self.c.get_task(tid)
        method, verb, needs_reason = {
            APPROVED: ("approve", "approve", False),
            REJECTED: ("reject", "reject", True),
            COMPLETED: ("complete", "accept the work on", False),
            IN_PROGRESS: ("return_to_work", "send back", True),
            CANCELLED: ("cancel", "cancel", True),
        }[to_state]
        self.c.check_human_decision(tid, to_state)  # refuse before asking for anything
        if to_state == COMPLETED:
            say("")
            say(FINAL_CHECK_REMINDER)
            handoffs = self.c.list_handoffs(tid)
            for h in handoffs:
                if h["response"] is not None and h["action"] in (IMPLEMENT, REVIEW):
                    say(f"  {h['id']}: {reply_label(h, task, handoffs)}")
            if not self.confirm("Have you checked the pull request and its test results on GitHub yourself?"):
                say("Then check them first. Nothing was changed.")
                return
        reason = self.text("Reason (required): ") if needs_reason else ""
        details = cli.task_summary(task) if to_state == APPROVED else ()
        cli.human_banner(f"{verb} {tid} \"{task['name']}\"", details)
        getattr(self.c, method)(tid, self.owner_passphrase(), reason)
        say(f"{tid} is now {self.c.get_task(tid)['state']}.")
        if to_state == COMPLETED:
            say("Recorded in Dexter only. Merging the pull request on GitHub is a separate step that only you do.")

    # -- handoffs ----------------------------------------------------------------------
    def handoffs_menu(self):
        while True:
            choice = self.choose("HANDOFFS", [
                ("1", "Paste a reply from ChatGPT or Claude Code"),
                ("2", "Copy a waiting handoff again"),
                ("3", "Prepare a new handoff for a task"),
                ("4", "List all handoffs"),
                ("5", "Read a recorded reply"),
            ])
            step = {"1": self.paste_reply_menu, "2": self.copy_again_menu, "3": self.prepare_menu,
                    "4": self.list_handoffs, "5": self.read_reply_menu}[choice]
            self.guarded(step)

    def waiting_handoffs(self):
        project = self.current_project()
        return [h for t in self.c.list_tasks(project["id"]) if t["state"] not in TERMINAL_STATES
                for h in self.c.list_handoffs(t["id"]) if h["response"] is None and self.c.handoff_is_current(h["id"])]

    def describe_handoff(self, h):
        return f"{h['id']}  {h['action']} for {h['task']} -> {role_title(h['to_role']).split(' (')[0]}"

    def paste_reply_menu(self):
        waiting = self.waiting_handoffs()
        if not waiting:
            say("No handoff is waiting for a reply. Prepare one from the task (Next step).")
            return
        h = self.pick("Which handoff is the reply for?", waiting, self.describe_handoff)
        self.paste_reply(h["id"])

    def copy_again_menu(self):
        waiting = self.waiting_handoffs()
        if not waiting:
            say("No handoff is waiting for a reply.")
            return
        self.send_handoff(self.pick("Which handoff?", waiting, self.describe_handoff)["id"])

    def prepare_menu(self):
        project = self.current_project()
        tasks = [t for t in self.c.list_tasks(project["id"])
                 if t["state"] in (BACKLOG, PLANNED, AWAITING_APPROVAL, IN_PROGRESS, IN_REVIEW)]
        if not tasks:
            say("No task needs a handoff right now. Tasks need handoffs while they are planned, in progress or "
                "in review.")
            return
        task = self.pick("Which task?", tasks, self.describe_task)
        self.prepare_handoff(task["id"], None)

    def list_handoffs(self):
        project = self.current_project()
        found = False
        for t in self.c.list_tasks(project["id"]):
            handoffs = self.c.list_handoffs(t["id"])
            for h in handoffs:
                found = True
                if h["response"] is None:
                    status = "waiting for a reply" if self.c.handoff_is_current(h["id"]) else \
                        "out of date (the task moved on); no reply needed"
                else:
                    status = f"reply recorded, outcome {h['response']['outcome'] or 'unstructured'}, {UNVERIFIED}"
                say(f"  {self.describe_handoff(h)}: {status}")
        if not found:
            say("No handoffs yet.")
        self.pause()

    def read_reply_menu(self):
        project = self.current_project()
        recorded = [h for t in self.c.list_tasks(project["id"]) for h in self.c.list_handoffs(t["id"])
                    if h["response"] is not None]
        if not recorded:
            say("No replies recorded yet.")
            return
        h = self.pick("Which reply?", recorded, self.describe_handoff)
        task = self.c.get_task(h["task"])
        say("")
        for line in unverified_banner(h["response"]["claimed_from"]):
            say(line)
        say(reply_label(h, task, self.c.list_handoffs(task["id"])))
        cli.show_flags(h["response"]["flags"])
        say("----- RECORDED REPLY (untrusted data; control characters removed) -----")
        say(h["response"]["raw"])
        say("----- END OF RECORDED REPLY -----")
        self.pause()

    def prepare_handoff(self, tid, action):
        task = self.c.get_task(tid)
        say("")
        say("Dexter will prepare a message for an AI assistant. It is NOT sent anywhere: you copy it yourself.")
        notes = "\n".join(self.lines("Notes for the assistant (optional), for example a branch name or a "
                                     "question you want answered:"))
        refs = self.lines("Files, branches or links to include (optional), one per line:")
        h = self.c.create_handoff(tid, action=action, context=notes, references=refs)
        say(f"Prepared {h['id']} for {role_title(h['to_role'])}: {ACTIONS[h['action']]}")
        self.send_handoff(h["id"])

    def send_handoff(self, handoff_id):
        h = self.c.get_handoff(handoff_id)
        path = self.ex.save_handoff(h)
        say("")
        say(f"{h['id']} is ready for {role_title(h['to_role'])}. Dexter has NOT sent it anywhere.")
        say(f"A copy is saved in {path}")
        say("Read it before you send it: anyone who can run Dexter can add notes to a handoff.")
        say(WHERE_TO_PASTE[h["to_role"]])
        while True:
            options = []
            if not self.linux:
                options.append(("1", "Copy it to the clipboard"))
            options.append(("2", "Open it in Notepad" if self.windows else "Show where the file is"))
            options += [("3", "Show it here on screen"),
                        ("4", "I have pasted it - record that (owner passphrase, optional)"),
                        ("5", "Done for now (paste the reply later from the Handoffs menu)")]
            choice = self.choose(f"How do you want to copy {h['id']}?", options)
            if choice == "1":
                ok, reason = copy_to_clipboard(h["message"])
                say("Copied. Now paste it (Ctrl+V) into the assistant." if ok else
                    f"Could not copy: {reason}. Try Notepad or the on-screen copy instead.")
            elif choice == "2":
                if self.windows and open_text_file(path):
                    say("Opened in Notepad. Select everything (Ctrl+A), copy (Ctrl+C), then paste it into the assistant.")
                else:
                    say(f"Open this file in a text editor and copy everything in it:\n  {path}")
            elif choice == "3":
                say("")
                say("----- COPY EVERYTHING BELOW THIS LINE -----")
                say(h["message"].rstrip("\n"))
                say("----- COPY EVERYTHING ABOVE THIS LINE -----")
            elif choice == "4":
                if h["delivery"] != "NOT_DELIVERED":
                    say(f"{h['id']}: {h['delivery_label']}")
                    continue
                cli.human_banner(f"confirm that you copied {h['id']} to {role_title(h['to_role'])}")
                h = self.c.mark_handoff_sent(h["id"], self.owner_passphrase())
                say(f"{h['id']}: {h['delivery_label']}")
            else:
                say(f"When {role_title(h['to_role']).split(' (')[0]} answers, choose [H] Handoffs > Paste a reply, "
                    "or [N] Do the next step.")
                return

    def paste_reply(self, handoff_id):
        h = self.c.get_handoff(handoff_id)
        say("")
        say(f"PASTE THE REPLY TO {h['id']} from {role_title(h['to_role'])}")
        say("Copy the assistant's WHOLE answer, including the ```json block at the end.")
        file_label = "Paste it into Notepad (recommended)" if self.windows else "Paste it into a reply file"
        choice = self.choose("How do you want to paste it?", [("1", file_label), ("2", "Paste it here in this window")],
                             default="1")
        if choice == "1":
            path = self.ex.reply_file(h["id"])
            if self.windows and open_text_file(path):
                say("Notepad is open. Paste the reply (Ctrl+V), save it (Ctrl+S) and close Notepad.")
            else:
                say(f"Open this file in a text editor, paste the reply, and save it:\n  {path}")
            self.ask("Then press Enter here...")
            text = self.ex.read_reply(path)
        else:
            path = None
            say("Paste the reply now. When it has all appeared, type END on a line by itself and press Enter.")
            collected, size = [], 0
            while True:
                line = self.ask("")
                if line.strip() == "END":
                    break
                collected.append(line)
                size += len(line) + 1
                if size > MAX_RESPONSE_CHARS:
                    say("The reply is too long to record.")
                    return
            text = "\n".join(collected)
        if not text.strip():
            say("The reply is empty. Nothing was recorded; try again when you have the reply.")
            return
        self.review_and_record(h, text, path)

    def review_and_record(self, h, text, path):
        structured = True
        try:
            preview = self.c.preview_response(h["id"], text)
        except ValidationError as exc:
            say("")
            say(f"Dexter could not read this reply: {exc}")
            say("Usually the assistant changed the JSON block, or part of the reply was not copied.")
            choice = self.choose("What now?", [
                ("1", "Cancel, and ask the assistant to answer again in the exact format"),
                ("2", "Keep it as an UNSTRUCTURED reply (stored for the record; it cannot move the task on)")],
                default="1")
            if choice == "1":
                say("Nothing was recorded." + (f" Your pasted text stays in {path}" if path else ""))
                return
            structured = False
            preview = self.c.preview_response(h["id"], text, structured=False)
        say("")
        for line in unverified_banner(preview["claimed_from"]):
            say(line)
        details = [f"Handoff: {preview['handoff']} for {preview['task']}",
                   f"Claimed sender: {role_title(preview['claimed_from'])} (Dexter cannot verify who wrote it)"]
        if structured:
            data = preview["data"]
            details += [f"Outcome: {preview['outcome']}", f"Summary: {data['summary']}"]
            for t in data.get("tests", []):
                details.append(f"Claimed test result: {t['result']} {t['name']}")
            for f in data.get("findings", []):
                details.append(f"Finding: [{f['severity']}] {f['title']}")
        else:
            details.append("UNSTRUCTURED: Dexter cannot check it, and it will not count as evidence")
        cli.show_flags(preview["flags"])
        cli.human_banner("record this reply exactly as pasted. Only confirm if you pasted it from that "
                         "assistant yourself", details)
        self.c.record_response(h["id"], text, self.owner_passphrase(), structured=structured)
        if path:
            self.ex.discard_reply(path)
        say(f"Recorded the reply to {h['id']} as an {UNVERIFIED}. Recording a reply never changes a task's state.")

    # -- reports, dashboard and checks ------------------------------------------------
    def reports_menu(self):
        while True:
            choice = self.choose("REPORTS AND HISTORY", [
                ("1", "Show the progress report"), ("2", "Save the progress report to a file"),
                ("3", "Show the project's recent history"), ("4", "Show one task's history")])
            self.guarded({"1": self.show_report, "2": self.save_report, "3": self.show_history,
                          "4": self.task_history}[choice])

    def show_report(self):
        from .report import progress_report

        say(progress_report(self.c, self.current_project()["id"], self.clock()).rstrip("\n"))
        self.pause()

    def save_report(self):
        from .report import progress_report

        project = self.current_project()
        now = self.clock()
        path = self.ex.save_report(project["id"], stamp(now), progress_report(self.c, project["id"], now))
        say(f"Saved the report to {path}")

    def show_history(self, task_key=None):
        if task_key:
            events = self.c.history(task_key=task_key)
        else:
            events = self.c.history(project_key=self.current_project()["id"], limit=None)[-30:]
            say("The 30 most recent entries:")
        cli.show_history(events)
        self.pause()

    def task_history(self):
        tasks = self.c.list_tasks(self.current_project()["id"])
        if not tasks:
            say("No tasks yet.")
            return
        self.show_history(task_key=self.pick("Which task?", tasks, self.describe_task)["id"])

    def open_dashboard(self):
        now = self.clock()
        page = dashboard.render_html(dashboard.build(self.c), self.c.store.path, now)
        path = self.ex.write_dashboard(page)
        if open_in_browser(path):
            say(f"Opened the read-only dashboard in your browser ({path}).")
        else:
            say(f"Open this file in your web browser: {path}")
        say("It is a snapshot with no buttons. Choose [D] again to refresh it.")

    def check_menu(self):
        while True:
            choice = self.choose("CHECK RECORDS AND BACK UP", [
                ("1", "Check the records for damage or tampering"),
                ("2", "Full check, including every owner signature (owner passphrase)"),
                ("3", "Back up the database now"),
                ("4", "Change the owner passphrase")])
            self.guarded({"1": self.check, "2": lambda: self.check(owner=True), "3": self.backup,
                          "4": self.change_passphrase}[choice])

    def check(self, owner=False):
        passphrase = None
        if owner:
            cli.human_banner("check every owner signature in the database")
            passphrase = self.owner_passphrase()
        problems = self.c.verify(passphrase=passphrase)
        for warning in self.c.permission_warnings():
            say(f"Warning: {warning}")
        if problems:
            say(f"Found {len(problems)} problem(s):")
            for p in problems:
                say(f"  - {p}")
            say("Do not keep working in this file. See \"Recovering from errors\" in docs/COORDINATOR.md.")
        else:
            seq, digest = self.c.anchor()
            say("No problems found.")
            say(f"History anchor: entry {seq}, {digest[:16]}. Write it down somewhere outside this computer;")
            say("if a later check shows an older anchor, the file was replaced with an older copy.")
        self.pause()

    def backup(self):
        path = self.c.backup(self.ex.backup_path(stamp(self.clock())))
        say(f"Backed up the database to {path}")
        say("Keep backups away from AI tools, for example on a USB stick you unplug.")

    def change_passphrase(self):
        say("Changing the owner passphrase. Press Enter without typing to cancel.")
        old = self.owner_passphrase()
        new = self._passphrase("New owner passphrase: ")
        if not new:
            raise Back
        if new != self._passphrase("Type the new one again: "):
            say("The two passphrases do not match; nothing was changed.")
            return
        self.c.change_owner_passphrase(old, new)
        say("Owner passphrase changed.")
