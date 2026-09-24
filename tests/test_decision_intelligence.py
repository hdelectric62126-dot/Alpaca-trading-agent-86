import unittest

import pandas as pd

from decision_intelligence import (
    INSUFFICIENT,
    build_daily_technical_report,
    build_fundamental_report,
)


def make_daily_bars(start=80.0, end=100.0, count=220):
    step = (end - start) / (count - 1)
    closes = [start + i * step for i in range(count)]
    return pd.DataFrame({
        "open": [p - 0.1 for p in closes],
        "high": [p + 0.4 for p in closes],
        "low": [p - 0.4 for p in closes],
        "close": closes,
        "volume": [1_000_000] * count,
    })


def duration_rows(values, unit="USD"):
    rows = []
    for year, value in values:
        rows.append({
            "start": f"{year}-01-01",
            "end": f"{year}-12-31",
            "filed": f"{year + 1}-02-15",
            "form": "10-K",
            "val": value,
        })
    return {"units": {unit: rows}}


def point_rows(values, unit="USD"):
    rows = []
    for year, value in values:
        rows.append({
            "end": f"{year}-12-31",
            "filed": f"{year + 1}-02-15",
            "form": "10-K",
            "val": value,
        })
    return {"units": {unit: rows}}


def healthy_company_facts():
    return {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": duration_rows([
                    (2024, 100_000_000), (2025, 112_000_000),
                ]),
                "GrossProfit": duration_rows([
                    (2024, 40_000_000), (2025, 47_000_000),
                ]),
                "NetCashProvidedByUsedInOperatingActivities": duration_rows([
                    (2024, 16_000_000), (2025, 18_000_000),
                ]),
                "PaymentsToAcquirePropertyPlantAndEquipment": duration_rows([
                    (2024, 5_000_000), (2025, 6_000_000),
                ]),
                "EarningsPerShareDiluted": duration_rows([
                    (2024, 7.0), (2025, 8.0),
                ], unit="USD/shares"),
                "InventoryNet": point_rows([
                    (2024, 20_000_000), (2025, 21_000_000),
                ]),
                "StockholdersEquity": point_rows([
                    (2024, 60_000_000), (2025, 65_000_000),
                ]),
                "LongTermDebtCurrent": point_rows([(2025, 2_000_000)]),
                "LongTermDebtNoncurrent": point_rows([(2025, 12_000_000)]),
                "ShortTermBorrowings": point_rows([(2025, 1_000_000)]),
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": point_rows(
                    [(2025, 10_000_000)], unit="shares"
                ),
            },
        },
    }


class DecisionIntelligenceTests(unittest.TestCase):
    def test_daily_technical_requires_200_bars(self):
        report = build_daily_technical_report(make_daily_bars(count=100))
        self.assertFalse(report.available)
        self.assertFalse(report.allowed)
        self.assertIn(INSUFFICIENT, report.reason)

    def test_daily_technical_allows_clean_uptrend(self):
        bars = make_daily_bars()
        report = build_daily_technical_report(bars, current_price=float(bars["close"].iloc[-1]))
        self.assertTrue(report.available)
        self.assertTrue(report.allowed, report.reason)
        self.assertEqual(report.trend, "uptrend")
        self.assertGreater(report.sma50, report.sma200)
        self.assertIsNotNone(report.reward_risk)

    def test_daily_technical_blocks_long_term_downtrend(self):
        bars = make_daily_bars(start=120.0, end=80.0)
        report = build_daily_technical_report(bars, current_price=80.0)
        self.assertFalse(report.allowed)
        self.assertIn("200-day trend", report.reason)

    def test_fundamental_screen_uses_strict_numeric_thresholds(self):
        report = build_fundamental_report(
            healthy_company_facts(),
            current_price=100.0,
            max_pe=20.0,
            min_revenue_growth_pct=8.0,
            strict_value_screen=True,
        )
        self.assertTrue(report.available)
        self.assertTrue(report.allowed, report.reason)
        self.assertAlmostEqual(report.pe_ratio, 12.5, places=2)
        self.assertGreater(report.free_cash_flow, 0)
        self.assertGreater(report.revenue_growth_pct, 8.0)
        self.assertEqual(report.match_confidence_pct, 100.0)

    def test_fundamental_screen_flags_inventory_buildup(self):
        payload = healthy_company_facts()
        payload["facts"]["us-gaap"]["InventoryNet"] = point_rows([
            (2024, 20_000_000), (2025, 35_000_000),
        ])
        report = build_fundamental_report(payload, current_price=100.0)
        self.assertFalse(report.allowed)
        self.assertTrue(any("inventory growth" in flag for flag in report.red_flags))


if __name__ == "__main__":
    unittest.main()
