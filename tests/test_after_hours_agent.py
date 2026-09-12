import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from after_hours_agent import AfterHoursLearningAgent, ResearchCandidate


def make_bars(length=180):
    closes = [100 + ((index % 20) - 10) * 0.15 + index * 0.01 for index in range(length)]
    return pd.DataFrame({
        "open": closes,
        "high": [value + 0.25 for value in closes],
        "low": [value - 0.25 for value in closes],
        "close": closes,
        "volume": [100 + index % 7 for index in range(length)],
    })


class AfterHoursLearningAgentTests(unittest.TestCase):
    def setUp(self):
        self.current = ResearchCandidate(0.0035, 60, 0.0045, 0.005)

    def test_report_is_advisory_only_and_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            agent = AfterHoursLearningAgent(path, minimum_test_trades=1)
            report = agent.run({"AMD": make_bars()}, self.current)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(report["mode"], "ADVISORY_ONLY")
        self.assertFalse(report["automatic_rule_changes"])
        self.assertFalse(report["automatic_orders"])
        self.assertTrue(report["approval_required"])
        self.assertEqual(saved["mode"], "ADVISORY_ONLY")

    def test_insufficient_data_keeps_rules(self):
        agent = AfterHoursLearningAgent("unused.json")
        result = agent.study_symbol("AMD", make_bars(50), self.current)
        self.assertEqual(result["status"], "insufficient_data")

    def test_candidate_grid_includes_current_rules(self):
        candidates = AfterHoursLearningAgent.candidates(self.current)
        self.assertIn(self.current, candidates)


if __name__ == "__main__":
    unittest.main()
