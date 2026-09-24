import unittest

import pandas as pd

from intraday_intelligence import build_intraday_report
from risk_agent import RiskAgent


def minute_bars(start_price=100.0, end_price=104.0, count=390):
    index = pd.date_range(
        "2026-09-24 09:30",
        periods=count,
        freq="1min",
        tz="America/New_York",
    )
    step = (end_price - start_price) / max(1, count - 1)
    closes = [start_price + step * i for i in range(count)]
    return pd.DataFrame({
        "open": [p - 0.02 for p in closes],
        "high": [p + 0.05 for p in closes],
        "low": [p - 0.05 for p in closes],
        "close": closes,
        "volume": [1000 + (i % 50) * 5 for i in range(count)],
    }, index=index)


class IntradayIntelligenceTests(unittest.TestCase):
    def test_bullish_multi_timeframe_context_passes(self):
        report = build_intraday_report(minute_bars())
        self.assertTrue(report.available)
        self.assertTrue(report.allowed, report.reason)
        self.assertEqual(report.alignment, "bullish")
        self.assertIsNotNone(report.vwap)
        self.assertIsNotNone(report.relative_volume)
        self.assertEqual(report.five_minute.timeframe, "5m")
        self.assertEqual(report.fifteen_minute.timeframe, "15m")

    def test_bearish_alignment_below_vwap_blocks(self):
        report = build_intraday_report(minute_bars(start_price=104.0, end_price=100.0))
        self.assertTrue(report.available)
        self.assertFalse(report.allowed)
        self.assertEqual(report.alignment, "bearish")
        self.assertIn("below VWAP", report.reason)

    def test_insufficient_history_fails_closed(self):
        report = build_intraday_report(minute_bars(count=40))
        self.assertFalse(report.available)
        self.assertFalse(report.allowed)
        self.assertIn("insufficient live data", report.reason)


class FixedFractionalRiskTests(unittest.TestCase):
    def test_stop_distance_caps_position_size(self):
        agent = RiskAgent(
            minimum_score=60,
            max_trade_notional=1000,
            max_total_exposure=1000,
            max_open_positions=3,
            daily_profit_target=100,
            daily_loss_limit=100,
            paper_bankroll=500,
            max_risk_per_trade_pct=0.01,
        )
        decision = agent.assess(
            score=100,
            positions={},
            daily_pnl=0,
            stop_loss_pct=0.02,
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.notional, 250.0)

    def test_invalid_stop_distance_fails_closed(self):
        agent = RiskAgent(
            minimum_score=60,
            max_trade_notional=25,
            max_total_exposure=75,
            max_open_positions=3,
            daily_profit_target=10,
            daily_loss_limit=10,
        )
        decision = agent.assess(
            score=100,
            positions={},
            daily_pnl=0,
            stop_loss_pct=0,
        )
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "invalid stop-loss percentage")


if __name__ == "__main__":
    unittest.main()
