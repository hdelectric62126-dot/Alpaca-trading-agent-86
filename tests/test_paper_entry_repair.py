"""Regression tests for paper-trading deadlock and safe execution modeling."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock

from advanced_system import AdvancedDecisionEngine, AdvancedFeatureStore, RegimeSnapshot
from execution import PaperExecution
from journal import TradeJournal


class PaperDecisionRepairTests(unittest.TestCase):
    def setUp(self):
        self.journal = TradeJournal(":memory:")
        self.store = AdvancedFeatureStore(self.journal.connection)
        self.engine = AdvancedDecisionEngine(
            self.store, take_profit_pct=0.0045, stop_loss_pct=0.005
        )
        self.regime = RegimeSnapshot(
            "bull_trend", "bullish", "low", 1.0, 0.1, 0.0005, 2
        )
        self.features = {
            "signal_score": 100, "dip_pct": 0.005, "rsi": 40,
            "momentum_pct": 0.004, "relative_volume": 1.6,
            "price_vs_vwap_pct": 0.003, "intraday_alignment": "bullish",
            "daily_trend": "uptrend", "latest_green": True,
            "room_to_resistance_pct": 0.2, "research_passed": True,
            "news_count": 2, "filing_count": 1,
        }

    def tearDown(self):
        self.journal.close()

    def insert(self, symbol, decision, minute, outcome, strategy="breakout"):
        now = datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)
        row_id = self.store.record_opportunity(
            symbol=symbol, decision=decision, strategy=strategy,
            regime="bull_trend", price=100, score=100, probability=0.7,
            expected_value_pct=0.2, features=self.features,
            now=now, dedupe_seconds=0,
        )
        self.journal.connection.execute(
            "UPDATE advanced_opportunities SET outcome_30m_pct=? WHERE id=?",
            (outcome, row_id),
        )
        self.journal.connection.commit()

    def test_rejected_prefilters_cannot_poison_entry_probability(self):
        for i in range(30):
            self.insert("AMD", "REJECT_PREFILTER", i * 2, -0.2)
        self.insert("TSM", "ADVANCED_BLOCK", 0, 0.2)
        result = self.store.calibration("breakout", "bull_trend")
        self.assertEqual(result["sample"], 1)
        self.assertIsNone(result["probability"])

    def test_minute_by_minute_scans_are_not_independent_samples(self):
        for i in range(45):
            self.insert("AMD", "ADVANCED_BLOCK", i * 2, -0.2)
        result = self.store.calibration("breakout", "bull_trend")
        self.assertEqual(result["sample"], 3)  # 0, 30, and 60 minutes apart
        self.assertIsNone(result["probability"])

    def test_iex_sparse_bid_does_not_set_unrealistic_execution_cost(self):
        self.features.update(
            quote_feed="iex", spread_bps=250,
            bar_reference_price=100, ask=100.02,
        )
        decision = self.engine.evaluate(self.features, self.regime, (True, "ok", 0))
        self.assertTrue(decision.allowed, decision.reason)
        self.assertGreater(decision.expected_value_pct, 0)

    def test_iex_ask_premium_still_blocks_bad_execution(self):
        self.features.update(
            quote_feed="iex", spread_bps=250,
            bar_reference_price=100, ask=100.35,
        )
        decision = self.engine.evaluate(self.features, self.regime, (True, "ok", 0))
        self.assertFalse(decision.allowed)
        self.assertIn("after costs", decision.reason)

    def test_sip_expensive_spread_still_blocks(self):
        self.features.update(
            quote_feed="sip", spread_bps=250,
            bar_reference_price=100, ask=100.02,
        )
        decision = self.engine.evaluate(self.features, self.regime, (True, "ok", 0))
        self.assertFalse(decision.allowed)
        self.assertIn("after costs", decision.reason)

    def test_router_strategy_identity_survives_enrichment(self):
        votes = self.engine.ensemble.vote(self.features)
        alternate = next(v.name for v in votes if v.name != votes[0].name)
        decision = self.engine.evaluate(
            self.features, self.regime, (True, "ok", 0),
            preferred_strategy=alternate,
        )
        self.assertEqual(decision.strategy, alternate)

    def test_safe_candidate_can_be_submitted_and_fill_verified_in_paper(self):
        self.features.update(
            quote_feed="iex", spread_bps=180,
            bar_reference_price=100, ask=100.02,
        )
        decision = self.engine.evaluate(self.features, self.regime, (True, "ok", 0))
        self.assertTrue(decision.allowed, decision.reason)
        client = Mock()
        client.submit_order.return_value = NS(
            id="simulated-paper-001", status="filled",
            filled_qty="0.25", filled_avg_price="100.02",
            filled_at=datetime.now(timezone.utc),
        )
        execution = PaperExecution(client, self.journal)
        execution.submit("AMD", "buy", score=100, notional=25,
                         reference_price=100.02)
        client.submit_order.assert_called_once()
        trades = self.journal.connection.execute(
            "SELECT * FROM paper_trades WHERE fill_verified=1"
        ).fetchall()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["status"], "OPEN")
        self.assertAlmostEqual(trades[0]["entry_price"], 100.02)


if __name__ == "__main__":
    unittest.main()
