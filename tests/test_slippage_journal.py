import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock

from execution import PaperExecution
from journal import PerformanceAnalyzer, TradeJournal


class SlippageJournalTests(unittest.TestCase):
    def setUp(self):
        self.journal = TradeJournal(":memory:")
        self.client = Mock()
        self.execution = PaperExecution(self.client, self.journal)
        self.client.submit_order.side_effect = self.accept

    def tearDown(self):
        self.journal.close()

    def accept(self, order_data):
        return NS(
            id=order_data.client_order_id,
            client_order_id=order_data.client_order_id,
            symbol=order_data.symbol,
            side=order_data.side,
            status="new",
            filled_qty="0",
        )

    def fill(self, order, quantity, price):
        order.status = "filled"
        order.filled_qty = str(quantity)
        order.filled_avg_price = str(price)
        order.filled_at = datetime.now(timezone.utc)
        self.execution.reconcile_one(order.client_order_id, order)

    def test_entry_and_exit_slippage_and_rationale_are_persisted(self):
        buy = self.execution.submit(
            "AMD",
            "buy",
            score=80,
            quantity=1,
            reference_price=100,
            rationale="signal_score=80; intraday_alignment=bullish",
        )
        self.fill(buy, 1, 100.10)

        sell = self.execution.submit(
            "AMD",
            "sell",
            quantity=1,
            entry_price=100.10,
            reference_price=101.00,
            rationale="trailing profit protection",
        )
        self.fill(sell, 1, 100.90)

        closed = self.journal.connection.execute(
            "SELECT * FROM paper_trades WHERE status='CLOSED'"
        ).fetchone()
        self.assertIsNotNone(closed)
        self.assertAlmostEqual(closed["entry_slippage_bps"], 10.0, places=5)
        self.assertAlmostEqual(closed["exit_slippage_bps"], 9.900990099, places=5)
        self.assertIn("intraday_alignment=bullish", closed["entry_reason"])
        self.assertEqual(closed["exit_reason"], "trailing profit protection")

        report = PerformanceAnalyzer(self.journal).report(1)
        self.assertAlmostEqual(report["average_entry_slippage_bps"], 10.0, places=5)
        self.assertAlmostEqual(report["average_exit_slippage_bps"], 9.9, places=2)


if __name__ == "__main__":
    unittest.main()
