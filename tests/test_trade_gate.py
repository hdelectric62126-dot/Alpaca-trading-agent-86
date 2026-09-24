import unittest

import pandas as pd

from trade_gate import assess_market_regime, build_technical_plan


def make_bars(closes, *, last_volume=200, green_last=True):
    opens = list(closes)
    if green_last:
        opens[-1] = closes[-1] - 0.15
    volumes = [100] * len(closes)
    volumes[-1] = last_volume
    return pd.DataFrame({
        "open": opens,
        "high": [price + 0.8 for price in closes],
        "low": [price - 0.3 for price in closes],
        "close": closes,
        "volume": volumes,
    })


class TradeGateTests(unittest.TestCase):
    def test_quality_gate_accepts_confirmed_recovery_with_room(self):
        closes = [99.0] * 10 + [98.7, 98.8, 98.9, 99.0, 99.1, 99.2, 99.35, 99.5, 99.7, 100.0]
        plan = build_technical_plan(
            make_bars(closes),
            take_profit_pct=0.0045,
            stop_loss_pct=0.005,
        )
        self.assertTrue(plan.allowed, plan.reason)
        self.assertTrue(plan.vwap_reclaimed)
        self.assertGreaterEqual(plan.volume_ratio, 1.0)
        self.assertGreaterEqual(plan.reward_risk, 1.0)

    def test_quality_gate_blocks_below_vwap(self):
        bars = make_bars([100.0] * 19 + [99.0])
        plan = build_technical_plan(
            bars,
            take_profit_pct=0.0045,
            stop_loss_pct=0.005,
        )
        self.assertFalse(plan.allowed)
        self.assertIn("VWAP", plan.reason)

    def test_market_regime_blocks_when_both_benchmarks_weaken(self):
        falling = make_bars([101.0, 100.9, 100.8, 100.7, 100.6,
                             100.5, 100.4, 100.3, 100.2, 100.0])
        regime = assess_market_regime({"SPY": falling, "QQQ": falling})
        self.assertFalse(regime.allowed)
        self.assertEqual(set(regime.weak_benchmarks), {"SPY", "QQQ"})

    def test_market_regime_allows_when_one_benchmark_is_not_weak(self):
        falling = make_bars([101.0, 100.9, 100.8, 100.7, 100.6,
                             100.5, 100.4, 100.3, 100.2, 100.0])
        rising = make_bars([100.0, 100.1, 100.2, 100.3, 100.4,
                            100.5, 100.6, 100.7, 100.8, 101.0])
        regime = assess_market_regime({"SPY": falling, "QQQ": rising})
        self.assertTrue(regime.allowed)

    def test_market_regime_fails_closed_without_benchmarks(self):
        regime = assess_market_regime({})
        self.assertFalse(regime.allowed)
        self.assertIn("unavailable", regime.reason)


if __name__ == "__main__":
    unittest.main()
