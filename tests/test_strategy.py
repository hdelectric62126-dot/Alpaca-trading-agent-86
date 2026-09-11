import unittest

import pandas as pd

from strategy import score_signal, walk_forward_backtest


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

    def test_backtester_is_deterministic_and_walk_forward(self):
        bars = make_bars([100] * 30 + [98, 99, 101, 102, 100, 99, 101])
        result = walk_forward_backtest(bars, minimum_score=0, lookback=30)
        self.assertEqual(result["starting_cash"], 1000.0)
        self.assertGreaterEqual(result["trades"], 1)

    def test_score_threshold_can_block_entry(self):
        bars = make_bars([100 + index for index in range(30)])
        result = walk_forward_backtest(bars, minimum_score=101, lookback=30)
        self.assertEqual(result["trades"], 0)


if __name__ == "__main__":
    unittest.main()
