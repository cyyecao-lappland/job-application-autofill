"""Connection guard checks with temporary files; no live browser actions."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from connection_guard import Rejected, update


class ConnectionGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.target = {"browser": "edge-instance", "url": "https://example.test/form"}
        update(self.root, "init", self.target)

    def reserve(self, route="getTab", **extra):
        return update(self.root, "reserve", {**self.target, "tabId": "current-tab", "route": route, **extra})

    def finish(self, outcome, attempt=1, **extra):
        return update(self.root, "finish", {"attempt": attempt, "outcome": outcome, "settled": True, **extra})

    def test_alternate_route_after_settled_failure(self):
        self.reserve()
        self.finish("unattached")
        state = self.reserve("user.claimTab")
        self.assertEqual(len(state["attempts"]), 2)
        state = self.finish("verified", attempt=2, probePassed=True, **self.target)
        self.assertEqual(state["status"], "verified")

    def test_same_route_rejected(self):
        self.reserve()
        self.finish("unsupported")
        with self.assertRaises(Rejected):
            self.reserve()

    def test_third_attempt_rejected(self):
        self.reserve()
        self.finish("stale")
        self.reserve("tabs.get")
        self.finish("unattached", attempt=2)
        with self.assertRaises(Rejected):
            self.reserve("user.claimTab")

    def test_pending_survives_reload_and_cannot_reset(self):
        self.reserve()
        self.assertEqual(update(self.root, "show", {})["status"], "pending")
        with self.assertRaises(Rejected):
            update(self.root, "init", self.target)
        with self.assertRaises(Rejected):
            self.reserve("user.claimTab")

    def test_occupied_does_not_fallback(self):
        self.reserve()
        self.finish("occupied")
        with self.assertRaises(Rejected):
            self.reserve("user.claimTab")

    def test_unsettled_or_timeout_does_not_fallback(self):
        self.reserve()
        self.finish("timeout", settled=False)
        with self.assertRaises(Rejected):
            self.reserve("tabs.get")

    def test_wrong_browser_or_url_does_not_consume_attempt(self):
        for extra in ({"browser": "other"}, {"url": "https://example.test/other"}):
            with self.assertRaises(Rejected):
                self.reserve(**extra)
        self.assertEqual(update(self.root, "show", {})["attempts"], [])

    def test_verification_requires_matching_probe(self):
        self.reserve()
        with self.assertRaises(Rejected):
            self.finish("verified", probePassed=True, **{**self.target, "url": "wrong"})
        self.assertEqual(update(self.root, "show", {})["status"], "pending")

    def test_lock_blocks_state_change(self):
        (self.root / "connection-state.lock").write_text("", encoding="utf-8")
        with self.assertRaises(Rejected):
            self.reserve()

    def test_cli_reserves_before_dispatch_and_blocks_next_process(self):
        command = [sys.executable, str(Path(__file__).with_name("connection_guard.py")),
                   "--run", str(self.root), "reserve", "--route", "getTab",
                   "--browser", self.target["browser"], "--url", self.target["url"],
                   "--tab-id", "current-tab"]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        output = json.loads(first.stdout)
        self.assertTrue(output["dispatchAllowed"])
        self.assertEqual(output["operationTimeoutMs"], 120000)
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(second.returncode, 1)
        self.assertFalse(json.loads(second.stdout)["dispatchAllowed"])


if __name__ == "__main__":
    unittest.main()
