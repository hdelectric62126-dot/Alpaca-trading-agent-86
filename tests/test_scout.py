import unittest

import pandas as pd

from scout import MarketScout


def make_bars(closes, last_volume=100):
    volumes = [100] * len(closes)
    volumes[-1] = last_volume
    return pd.DataFrame({
        "open": closes,
        "high": [price + 0.2 for price in closes],
        "low": [price - 0.2 for price in closes],
        "close": closes,
        "volume": volumes,
    })


def confirmed_dip(final=99.0, floor=98.4):
    return make_bars([100.0] * 25 + [floor + 0.1, floor, floor + 0.2, floor + 0.4, final], last_volume=200)


class ScoutTests(unittest.TestCase):
    def test_falling_dip_is_not_entry_ready(self):
        scout = MarketScout(dip_threshold=0.0035, minimum_score=0, top_n=1)
        falling = make_bars([100.0] * 29 + [99.0], last_volume=200)
        opportunity = scout.analyze("TSLA", falling)
        self.assertFalse(opportunity.reversal_confirmed)
        self.assertFalse(opportunity.entry_ready)

    def test_confirmed_reversal_can_be_entry_ready(self):
        scout = MarketScout(dip_threshold=0.0035, minimum_score=0, top_n=1)
        opportunity = scout.analyze("TSLA", confirmed_dip())
        self.assertTrue(opportunity.reversal_confirmed)
        self.assertTrue(opportunity.entry_ready)
        self.assertGreater(opportunity.price, opportunity.short_mean_price)

    def test_ranks_ready_opportunity_ahead_of_non_ready_symbol(self):
        scout = MarketScout(dip_threshold=0.0035, minimum_score=0, top_n=1)
        ready = confirmed_dip()
        flat = make_bars([100.0] * 30)
        ranked = scout.rank({"AMD": flat, "TSLA": ready})
        self.assertEqual(ranked[0].symbol, "TSLA")
        self.assertTrue(ranked[0].entry_ready)

    def test_only_top_n_eligible_setups_feed_execution(self):
        scout = MarketScout(dip_threshold=0.0035, minimum_score=0, top_n=1)
        ranked = scout.rank({
            "AMD": confirmed_dip(final=98.0, floor=97.4),
            "TSLA": confirmed_dip(final=99.0, floor=98.4),
        })
        candidates = scout.candidates(ranked)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].symbol, "AMD")

    def test_minimum_score_blocks_weak_setups(self):
        scout = MarketScout(dip_threshold=0.0035, minimum_score=101, top_n=3)
        ranked = scout.rank({"AMD": confirmed_dip(final=98.0, floor=97.4)})
        self.assertEqual(scout.candidates(ranked), [])


if __name__ == "__main__":
    unittest.main()
