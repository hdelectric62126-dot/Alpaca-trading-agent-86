import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from journal import PerformanceAnalyzer, TradeJournal
from strategy import score_signal


def make_bars(closes, volumes=None):
    volumes = volumes or [100] * len(closes)
    import pandas as pd
    return pd.DataFrame({"open": closes, "high": [p + 1 for p in closes],
                         "low": [p - 1 for p in closes], "close": closes,
                         "volume": volumes})


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.database = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.database.close()
        self.journal = TradeJournal(self.database.name)
        self.signal = score_signal(make_bars([100 + i * 0.1 for i in range(30)]))
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def tearDown(self):
        self.journal.close()
        os.unlink(self.database.name)

    def test_records_analysis_and_rejected_signal(self):
        self.journal.record_cycle(symbol="AMD", current_price=100,
                                  market_data={"close": 100, "volume": 100},
                                  signal=self.signal, decision="REJECT",
                                  rejection_reason="score below minimum",
                                  timestamp=self.timestamp)
        row = self.journal.connection.execute("SELECT * FROM analysis_cycles").fetchone()
        self.assertEqual(row["decision"], "REJECT")
        self.assertEqual(row["rejection_reason"], "score below minimum")
        self.assertEqual(json.loads(row["component_scores"])["dip"], 0)
        self.assertEqual(len(json.loads(row["reasons"])), 5)

    def test_profit_loss_and_drawdown(self):
        for symbol, score, pnl in (("AMD", 65, 10), ("TSM", 35, -4), ("TSLA", 85, 6)):
            self.journal.record_trade(symbol=symbol, score=score, quantity=1,
                                      entry_price=100, exit_price=100 + pnl,
                                      realized_pnl=pnl, status="CLOSED",
                                      opened_at=self.timestamp)
        report = PerformanceAnalyzer(self.journal).report(1)
        self.assertEqual(report["paper_trades"], 3)
        self.assertEqual(report["wins"], 2)
        self.assertEqual(report["losses"], 1)
        self.assertEqual(report["total_profit_loss"], 12)
        self.assertEqual(report["maximum_drawdown"], 4)
        self.assertEqual(report["by_score_range"]["20-39"]["losses"], 1)
        self.assertEqual(report["by_symbol"]["AMD"]["total_profit_loss"], 10)

    def test_report_cli_uses_database_without_alpaca(self):
        result = subprocess.run([sys.executable, "report.py", "--days", "7",
                                 "--db", self.database.name], capture_output=True,
                                text=True, check=True)
        self.assertIn("PAPER performance report (7 days)", result.stdout)
        self.assertIn('"signals": 0', result.stdout)


if __name__ == "__main__":
    unittest.main()