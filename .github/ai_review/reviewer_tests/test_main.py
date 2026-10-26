import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from dexter_review import __main__ as entry
from dexter_review import clients

from reviewer_tests.fakes import LIVE_ENV


def no_network(*args, **kwargs):
    raise AssertionError("network access attempted")


class EntryPointTests(unittest.TestCase):
    def run_main(self, env, event=None):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(env)
            if event is not None:
                path = os.path.join(tmp, "event.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(event, f)
                env["GITHUB_EVENT_PATH"] = path
            out = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                clients.urllib.request, "urlopen", no_network
            ), redirect_stdout(out):
                code = entry.main()
            return code, out.getvalue()

    def test_unset_mode_exits_cleanly_without_network(self):
        code, out = self.run_main({})
        self.assertEqual(code, 0)
        self.assertIn("disabled", out)

    def test_malformed_config_refuses_without_network(self):
        code, out = self.run_main(dict(LIVE_ENV, AI_REVIEW_MAX_COST_PER_RUN_USD="lots"))
        self.assertEqual(code, 1)
        self.assertIn("No API call was made", out)
        self.assertNotIn(LIVE_ENV["OPENAI_API_KEY"], out)

    def test_bad_event_refuses_without_network(self):
        code, out = self.run_main(LIVE_ENV, {"inputs": {"pr_number": "3 && curl evil"}})
        self.assertEqual(code, 1)
        self.assertIn("Refusing to run", out)

    def test_wrong_ref_refuses_without_network(self):
        code, out = self.run_main(dict(LIVE_ENV, GITHUB_REF="refs/heads/attacker"), {"inputs": {"pr_number": "3"}})
        self.assertEqual(code, 1)
        self.assertIn("refusing to run", out)

    def test_unexpected_error_does_not_print_traceback(self):
        with mock.patch.object(entry.Runner, "run", side_effect=KeyError("sensitive-value")):
            code, out = self.run_main(LIVE_ENV, {"inputs": {"pr_number": "3"}})
        self.assertEqual(code, 1)
        self.assertNotIn("sensitive-value", out)
        self.assertNotIn("Traceback", out)


if __name__ == "__main__":
    unittest.main()
