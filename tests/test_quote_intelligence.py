import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock

from quote_intelligence import LevelOneQuoteAgent


class QuoteIntelligenceTests(unittest.TestCase):
    def test_batch_quotes_compute_spread_bps(self):
        now = datetime(2026, 9, 25, 14, 30, tzinfo=timezone.utc)
        client = Mock()
        client.get_stock_latest_quote.return_value = {
            "AMD": NS(bid_price=100.00, ask_price=100.10, timestamp=now),
            "NVDA": NS(bid_price=200.00, ask_price=200.20, timestamp=now),
        }
        result = LevelOneQuoteAgent(client, max_age_seconds=120).fetch(
            ["AMD", "NVDA"], now=now
        )
        self.assertTrue(result["AMD"].available)
        self.assertAlmostEqual(result["AMD"].mid, 100.05, places=5)
        self.assertAlmostEqual(result["AMD"].spread_bps, 9.995002499, places=5)
        self.assertEqual(client.get_stock_latest_quote.call_count, 1)

    def test_stale_quote_fails_closed(self):
        now = datetime(2026, 9, 25, 14, 30, tzinfo=timezone.utc)
        client = Mock()
        client.get_stock_latest_quote.return_value = {
            "AMD": NS(
                bid_price=100,
                ask_price=100.05,
                timestamp=now - timedelta(minutes=5),
            )
        }
        result = LevelOneQuoteAgent(client, max_age_seconds=120).fetch(["AMD"], now=now)
        self.assertFalse(result["AMD"].available)
        self.assertIn("stale", result["AMD"].reason)

    def test_transport_failure_marks_all_symbols_unavailable(self):
        client = Mock()
        client.get_stock_latest_quote.side_effect = RuntimeError("outage")
        result = LevelOneQuoteAgent(client).fetch(["AMD", "NVDA"])
        self.assertFalse(result["AMD"].available)
        self.assertFalse(result["NVDA"].available)
        self.assertIn("request failed", result["AMD"].reason)


if __name__ == "__main__":
    unittest.main()
