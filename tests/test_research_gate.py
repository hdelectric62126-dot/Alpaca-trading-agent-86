import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from requests import ReadTimeout

from research_gate import ResearchGate, headline_has_risk, recent_material_filings


class ResearchGateTests(unittest.TestCase):
    def test_flags_high_risk_news_language(self):
        self.assertTrue(headline_has_risk("Company lowers guidance after cyberattack"))
        self.assertFalse(headline_has_risk("Company announces new product launch"))

    def test_flags_recent_material_sec_filing(self):
        now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
        result = recent_material_filings(
            ["8-K", "10-Q", "4"],
            ["2026-09-24", "2026-09-20", "2026-09-24"],
            now=now,
            lookback_days=1,
        )
        self.assertEqual(result, ("8-K filed 2026-09-24",))

    def test_watchlist_scanner_groups_news_in_one_cached_request(self):
        gate = ResearchGate("key", "secret", cache_seconds=300)
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "news": [
                {
                    "headline": "AMD launches new accelerator",
                    "summary": "new product",
                    "symbols": ["AMD", "NVDA"],
                },
                {
                    "headline": "NVDA lowers guidance",
                    "summary": "guidance cut",
                    "symbols": ["NVDA"],
                },
            ]
        }
        gate.session = Mock()
        gate.session.get.return_value = response
        now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)

        result = gate.scan_watchlist(["AMD", "NVDA"], now=now)
        self.assertEqual(result["AMD"]["count"], 1)
        self.assertEqual(result["AMD"]["risky_count"], 0)
        self.assertEqual(result["NVDA"]["count"], 2)
        self.assertEqual(result["NVDA"]["risky_count"], 1)
        self.assertEqual(gate.session.get.call_count, 1)

        cached = gate.scan_watchlist(["NVDA", "AMD"], now=now)
        self.assertEqual(cached, result)
        self.assertEqual(gate.session.get.call_count, 1)

    def test_ignores_old_or_non_material_forms(self):
        now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
        result = recent_material_filings(
            ["10-K", "4"],
            ["2026-09-20", "2026-09-24"],
            now=now,
            lookback_days=1,
        )
        self.assertEqual(result, ())


    def test_sec_unavailable_degrades_but_does_not_block_clean_setup(self):
        gate = ResearchGate("key", "secret", cache_seconds=0)
        gate._news = Mock(return_value=(("Company announces new product launch",), ()))
        gate._filings = Mock(side_effect=ReadTimeout("SEC timed out"))
        now = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)

        decision = gate.review("AMD", now=now)

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.sources_ok)
        self.assertIn("trusted research degraded", decision.reason)
        self.assertIn("SEC unavailable: ReadTimeout", decision.reason)

    def test_material_sec_filing_still_blocks(self):
        gate = ResearchGate("key", "secret", cache_seconds=0)
        gate._news = Mock(return_value=(("Company announces new product launch",), ()))
        gate._filings = Mock(return_value=("8-K filed 2026-09-28",))
        now = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)

        decision = gate.review("AMD", now=now)

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.sources_ok)
        self.assertIn("recent material SEC filing", decision.reason)



if __name__ == "__main__":
    unittest.main()
