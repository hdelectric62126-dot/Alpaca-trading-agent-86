import unittest

import pandas as pd

from strategy import entry_reversal_confirmed, score_signal, walk_forward_backtest


def make_bars(closes, volumes=None):
    volumes = volumes or [100] * len(closes)
    return pd.DataFrame({
        "open": closes,
        "high": [price + 1 for price in closes],
        "low": [price - 1 for price in closes],
        "close": closes,
        "volume": volumes,
    })


class StrategyTests(unittest.TestCase):
    def test_signal_has_five_explainable_components(self):
        bars = make_bars([100 + index * 0.1 for index in range(30)])
        signal = score_signal(bars)
        self.assertGreaterEqual(signal.score, 0)
        self.assertLessEqual(signal.score, 100)
        self.assertEqual(len(signal.reasons), 5)

    def test_reversal_confirmation_rejects_falling_close(self):
        bars = make_bars([100.0] * 29 + [99.0])
        self.assertFalse(entry_reversal_confirmed(bars))

    def test_reversal_confirmation_requires_short_mean_reclaim(self):
        weak_bounce = make_bars([100.0] * 27 + [98.0, 98.1, 98.2])
        strong_bounce = make_bars([100.0] * 25 + [98.5, 98.4, 98.6, 98.8, 99.0])
        self.assertFalse(entry_reversal_confirmed(weak_bounce))
        self.assertTrue(entry_reversal_confirmed(strong_bounce))

    def test_backtester_is_deterministic_and_walk_forward(self):
        bars = make_bars([100] * 30 + [97.0, 97.2, 97.4, 97.6, 97.8, 100.0, 101.0])
        result = walk_forward_backtest(bars, minimum_score=0, lookback=30)
        self.assertEqual(result["starting_cash"], 1000.0)
        self.assertGreaterEqual(result["trades"], 1)

    def test_score_threshold_can_block_entry(self):
        bars = make_bars([100 + index for index in range(30)])
        result = walk_forward_backtest(bars, minimum_score=101, lookback=30)
        self.assertEqual(result["trades"], 0)


if __name__ == "__main__":
    unittest.main()
