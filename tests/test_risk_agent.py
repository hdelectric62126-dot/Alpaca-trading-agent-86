import unittest
from types import SimpleNamespace

from risk_agent import RiskAgent


class RiskAgentTests(unittest.TestCase):
    def setUp(self):
        self.agent = RiskAgent(
            minimum_score=60, max_trade_notional=25,
            max_total_exposure=75, max_open_positions=3,
            daily_profit_target=10, daily_loss_limit=10,
        )

    @staticmethod
    def positions(*values):
        return {str(index): SimpleNamespace(market_value=value)
                for index, value in enumerate(values)}

    def test_blocks_daily_loss_limit(self):
        result = self.agent.assess(score=90, positions={}, daily_pnl=-10)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "daily loss limit reached")

    def test_blocks_position_count(self):
        result = self.agent.assess(score=90, positions=self.positions(10, 10, 10), daily_pnl=0)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "maximum open positions reached")

    def test_blocks_total_exposure(self):
        result = self.agent.assess(score=90, positions=self.positions(74.5), daily_pnl=0)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "portfolio exposure limit reached")

    def test_score_controls_size_but_never_exceeds_cap(self):
        minimum = self.agent.assess(score=60, positions={}, daily_pnl=0)
        strongest = self.agent.assess(score=100, positions={}, daily_pnl=0)
        self.assertEqual(minimum.notional, 12.50)
        self.assertEqual(strongest.notional, 25.00)

    def test_remaining_exposure_reduces_size(self):
        result = self.agent.assess(score=100, positions=self.positions(60), daily_pnl=0)
        self.assertTrue(result.approved)
        self.assertEqual(result.notional, 15.00)

    def test_virtual_bankroll_caps_available_cash(self):
        agent = RiskAgent(
            minimum_score=60, max_trade_notional=25,
            max_total_exposure=75, max_open_positions=3,
            daily_profit_target=10, daily_loss_limit=10,
            paper_bankroll=20,
        )
        result = agent.assess(score=100, positions=self.positions(15), daily_pnl=0)
        self.assertTrue(result.approved)
        self.assertEqual(result.notional, 5.00)

    def test_virtual_bankroll_can_exhaust(self):
        agent = RiskAgent(
            minimum_score=60, max_trade_notional=25,
            max_total_exposure=75, max_open_positions=3,
            daily_profit_target=10, daily_loss_limit=50,
            paper_bankroll=20,
        )
        result = agent.assess(score=100, positions={}, daily_pnl=-19.5)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "virtual paper bankroll exhausted")



if __name__ == "__main__":
    unittest.main()
