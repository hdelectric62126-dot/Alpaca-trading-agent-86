import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from guardian_agent import GuardianAgent
from exit_agent import ExitAgent


class GuardianTests(unittest.TestCase):
    def test_category_isolation_cooldown_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "journal.db")
            now = [1000.0]
            connection = sqlite3.connect(path)
            guardian = GuardianAgent(connection, 2, 60, lambda: now[0])
            guardian.record_failure("orders")
            self.assertTrue(guardian.entry_allowed())
            guardian.record_success("quotes")
            guardian.record_failure("orders")
            self.assertFalse(guardian.entry_allowed())
            connection.close()
            connection = sqlite3.connect(path)
            guardian = GuardianAgent(connection, 2, 60, lambda: now[0])
            self.assertFalse(guardian.entry_allowed())
            guardian.record_success("orders")
            self.assertFalse(guardian.entry_allowed())
            self.assertTrue(guardian.status()["exits_allowed"])
            now[0] = 1060
            self.assertTrue(guardian.entry_allowed())
            guardian.record_failure("orders")
            self.assertTrue(guardian.entry_allowed())
            destination = Path(directory) / "status.json"
            guardian.write_status(destination)
            self.assertTrue(json.loads(destination.read_text())["entry_allowed"])
            connection.close()

    def test_success_resets_consecutive_failures(self):
        with sqlite3.connect(":memory:") as connection:
            guardian = GuardianAgent(connection, 2)
            guardian.record_failure("quotes")
            guardian.record_success("quotes")
            guardian.record_failure("quotes")
            self.assertTrue(guardian.entry_allowed())


class DurableExitTests(unittest.TestCase):
    def test_restart_preserves_trailing_stop_and_changed_basis_resets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "journal.db")
            connection = sqlite3.connect(path)
            agent = ExitAgent(.01, .005, connection=connection)
            agent.decide("AMD", 100, pd.DataFrame({"close": [100.0, 100.1, 100.3, 100.5]}))
            connection.close()
            connection = sqlite3.connect(path)
            agent = ExitAgent(.01, .005, connection=connection)
            result = agent.decide("AMD", 100, pd.DataFrame({"close": [100.1, 100.15, 100.2, 100.25]}))
            self.assertEqual(result.reason, "trailing profit protection")
            result = agent.decide("AMD", 100.2, pd.DataFrame({"close": [100.1, 100.15, 100.2, 100.25]}))
            self.assertEqual(result.action, "HOLD")
            self.assertEqual(agent.high_water["AMD"], 100.25)
            agent.clear("AMD")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM exit_high_water").fetchone()[0], 0)
            connection.close()

    def test_new_position_identity_resets_same_price_basis(self):
        agent = ExitAgent(.01, .005)
        agent.decide("AMD", 100, pd.DataFrame({"close": [100.0, 100.1, 100.3, 100.5]}), entry_identity="first")
        result = agent.decide("AMD", 100, pd.DataFrame({"close": [100.1, 100.15, 100.2, 100.25]}), entry_identity="second")
        self.assertEqual(result.action, "HOLD")
        self.assertEqual(agent.high_water["AMD"], 100.25)

