import unittest

from paper_tuning import resolve_paper_sizing


class PaperTuningTests(unittest.TestCase):
    def test_baseline_is_unchanged(self):
        result = resolve_paper_sizing(
            "baseline", max_trade_notional=25,
            max_total_exposure=75, max_open_positions=3,
        )
        self.assertEqual(result.max_trade_notional, 25)
        self.assertEqual(result.max_total_exposure, 75)
        self.assertEqual(result.max_open_positions, 3)

    def test_size2x_is_bounded(self):
        result = resolve_paper_sizing(
            "size2x", max_trade_notional=25,
            max_total_exposure=75, max_open_positions=3,
        )
        self.assertEqual(result.max_trade_notional, 50)
        self.assertEqual(result.max_total_exposure, 100)
        self.assertEqual(result.max_open_positions, 3)

    def test_size2x_never_exceeds_hard_caps(self):
        result = resolve_paper_sizing(
            "size2x", max_trade_notional=40,
            max_total_exposure=90, max_open_positions=8,
        )
        self.assertEqual(result.max_trade_notional, 50)
        self.assertEqual(result.max_total_exposure, 100)
        self.assertEqual(result.max_open_positions, 3)

    def test_unknown_profile_fails_closed(self):
        with self.assertRaises(ValueError):
            resolve_paper_sizing(
                "aggressive", max_trade_notional=25,
                max_total_exposure=75, max_open_positions=3,
            )


if __name__ == "__main__":
    unittest.main()
