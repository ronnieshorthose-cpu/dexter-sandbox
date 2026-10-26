"""Helpers for driving the guided menu in tests: scripted answers, counted passphrases."""

import contextlib
import io
from unittest import mock

from dexter_coordinator import guided
from dexter_coordinator.guided import Guide

from tests.coordinator_support import PASS, CoordinatorTestCase, response_text


class Script:
    """Answers typed at the menu, in order. A callable answer runs first (for example to
    write a reply file) and its return value is typed. Running out ends the session (EOF)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        answer = self.answers.pop(0)
        return answer() if callable(answer) else answer


class Passphrases:
    """Stands in for the interactive passphrase prompt and counts how often it is asked."""

    def __init__(self, *values, default=PASS):
        self.values = list(values)
        self.default = default
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        return self.values.pop(0) if self.values else self.default


class GuidedTestCase(CoordinatorTestCase):
    platform = "win32"

    def guide(self, answers, *, passphrases=None, platform=None):
        self.script = Script(answers)
        self.passphrases = passphrases or Passphrases()
        return Guide(self.c, ask=self.script, passphrase=self.passphrases, platform=platform or self.platform,
                     clock=self.clock)

    def run_guide(self, answers, **kwargs):
        """Run the whole menu until the answers run out. Returns everything it printed."""
        g = self.guide(answers, **kwargs)
        out = io.StringIO()
        self.opened = []
        with contextlib.redirect_stdout(out), \
                mock.patch.object(guided, "open_text_file", lambda path: self.opened.append(path) or False), \
                mock.patch.object(guided, "open_in_browser", lambda path: self.opened.append(path) or False), \
                mock.patch.object(guided, "copy_to_clipboard", lambda text: (True, "")):
            self.assertEqual(g.run(), 0)
        self.g = g
        self.out = out.getvalue()
        return self.out

    def run_step(self, step, answers, **kwargs):
        """Run one guided step (a bound method name and its arguments) through the menu's guard."""
        g = self.guide(answers, **kwargs)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(guided, "open_text_file", lambda path: False), \
                mock.patch.object(guided, "copy_to_clipboard", lambda text: (True, "")):
            try:
                g.guarded(getattr(g, step[0]), *step[1:])
            except guided.Quit:
                pass
        self.g = g
        self.out = out.getvalue()
        return self.out

    def reply_into_file(self, handoff_id, text=None, **overrides):
        """An answer that saves a reply into the handoff's inbox file, as the owner would in Notepad."""
        def save():
            g = self.g_for_files()
            h = self.c.get_handoff(handoff_id)
            path = g.reply_file(handoff_id)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text if text is not None else response_text(h, **overrides))
            return ""
        return save

    def g_for_files(self):
        from dexter_coordinator.exchange import Exchange

        return Exchange(self.path)
