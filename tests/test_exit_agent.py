import unittest

import pandas as pd

from exit_agent import ExitAgent


def bars(closes):
    return pd.DataFrame({"close": closes})


class ExitAgentTests(unittest.TestCase):
    def make_agent(self):
        return ExitAgent(0.0045, 0.0050, 0.0035, 0.0020)

    def test_hard_stop_always_sells(self):
        decision = self.make_agent().decide("AMD", 100, bars([100, 99.8, 99.6, 99.49]))
        self.assertEqual(decision.action, "SELL")
        self.assertEqual(decision.reason, "hard stop loss")

    def test_positive_momentum_can_hold_after_base_target(self):
        decision = self.make_agent().decide("AMD", 100, bars([100, 100.1, 100.3, 100.5]))
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.reason, "profit protection armed")

    def test_extended_target_locks_profit(self):
        decision = self.make_agent().decide("AMD", 100, bars([100, 100.2, 100.4, 100.7]))
        self.assertEqual(decision.action, "SELL")
        self.assertEqual(decision.reason, "extended profit target")

    def test_trailing_pullback_locks_profit(self):
        agent = self.make_agent()
        agent.decide("AMD", 100, bars([100, 100.1, 100.3, 100.5]))
        decision = agent.decide("AMD", 100, bars([100.5, 100.4, 100.35, 100.29]))
        self.assertEqual(decision.action, "SELL")
        self.assertEqual(decision.reason, "trailing profit protection")

    def test_clear_removes_old_high_water(self):
        agent = self.make_agent()
        agent.decide("AMD", 100, bars([100, 100.1, 100.3, 100.5]))
        agent.clear("AMD")
        self.assertNotIn("AMD", agent.high_water)


if __name__ == "__main__":
    unittest.main()
