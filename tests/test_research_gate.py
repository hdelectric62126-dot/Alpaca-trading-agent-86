import unittest
from datetime import datetime, timezone

from research_gate import headline_has_risk, recent_material_filings


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

    def test_ignores_old_or_non_material_forms(self):
        now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
        result = recent_material_filings(
            ["10-K", "4"],
            ["2026-09-20", "2026-09-24"],
            now=now,
            lookback_days=1,
        )
        self.assertEqual(result, ())


if __name__ == "__main__":
    unittest.main()
