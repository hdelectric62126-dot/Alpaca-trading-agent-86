import os
import tempfile
import unittest
from datetime import datetime, timezone

from journal import TradeJournal
from performance_agent import PerformanceAgent


class PerformanceAgentTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.path = handle.name
        handle.close()
        self.journal = TradeJournal(self.path)

    def tearDown(self):
        self.journal.close()
        os.unlink(self.path)

    def add_trade(self, symbol, score, pnl):
        self.journal.record_trade(
            symbol=symbol, score=score, quantity=1, entry_price=100,
            exit_price=100 + pnl, realized_pnl=pnl, status="CLOSED",
            opened_at=datetime.now(timezone.utc).isoformat(),
        )

    def test_waits_for_safe_sample_size_and_saves_snapshot(self):
        self.add_trade("AMD", 65, 2)
        result = PerformanceAgent(self.journal, minimum_sample=3).analyze(7)
        self.assertIn("at least 3", result["recommendations"][0])
        count = self.journal.connection.execute(
            "SELECT COUNT(*) FROM performance_snapshots"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_flags_weak_symbol_and_score_range(self):
        for pnl in (-2, -1, -3):
            self.add_trade("TSLA", 45, pnl)
        advice = PerformanceAgent(self.journal, minimum_sample=3).analyze(7)["recommendations"]
        self.assertTrue(any("TSLA" in item for item in advice))
        self.assertTrue(any("40-59" in item for item in advice))

    def test_never_changes_configuration(self):
        self.add_trade("AMD", 80, 2)
        before = dict(os.environ)
        PerformanceAgent(self.journal, minimum_sample=1).analyze(7)
        self.assertEqual(dict(os.environ), before)


if __name__ == "__main__":
    unittest.main()
