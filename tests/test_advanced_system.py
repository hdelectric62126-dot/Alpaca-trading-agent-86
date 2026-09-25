import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pandas as pd

from advanced_system import (
    AdvancedDecisionEngine,
    AdvancedFeatureStore,
    AdvancedOpportunityRouter,
    ChampionChallengerLab,
    MarketRegimeClassifier,
    PortfolioRiskModel,
    StrategyEnsemble,
)
from journal import TradeJournal


def bars(start=100.0, count=30, direction=1.0, wiggle=0.05):
    index = pd.date_range(
        "2026-09-24 10:00", periods=count, freq="1min", tz="America/New_York"
    )
    closes = [
        start + direction * i * 0.08 + ((i % 3) - 1) * wiggle
        for i in range(count)
    ]
    return pd.DataFrame(
        {
            "open": [p - 0.03 for p in closes],
            "high": [p + 0.08 for p in closes],
            "low": [p - 0.08 for p in closes],
            "close": closes,
            "volume": [1000 + i * 10 for i in range(count)],
        },
        index=index,
    )


class AdvancedSystemTests(unittest.TestCase):
    def setUp(self):
        self.journal = TradeJournal(":memory:")
        self.store = AdvancedFeatureStore(self.journal.connection)

    def tearDown(self):
        self.journal.close()

    def test_regime_classifier_identifies_bullish_benchmarks(self):
        result = MarketRegimeClassifier().classify(
            {"SPY": bars(), "QQQ": bars(start=200)}
        )
        self.assertEqual(result.trend, "bullish")
        self.assertGreater(result.breadth, 0.9)
        self.assertEqual(result.usable_benchmarks, 2)

    def test_strategy_ensemble_returns_ranked_specialists(self):
        features = {
            "signal_score": 80,
            "dip_pct": 0.005,
            "rsi": 42,
            "momentum_pct": 0.002,
            "relative_volume": 1.5,
            "price_vs_vwap_pct": 0.002,
            "intraday_alignment": "bullish",
            "daily_trend": "uptrend",
            "latest_green": True,
            "room_to_resistance_pct": 0.2,
            "news_count": 1,
            "filing_count": 0,
            "research_passed": True,
        }
        votes = StrategyEnsemble().vote(features)
        self.assertEqual(len(votes), 6)
        self.assertGreaterEqual(votes[0].score, votes[-1].score)
        self.assertTrue(all(0 <= vote.score <= 100 for vote in votes))

    def test_counterfactual_tracker_records_future_outcomes(self):
        observed = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
        row_id = self.store.record_opportunity(
            symbol="AMD",
            decision="REJECT_PREFILTER",
            strategy="dip_reversal",
            regime="bull_trend",
            price=100,
            score=60,
            probability=0.55,
            expected_value_pct=0.01,
            features={"signal_score": 60},
            now=observed,
        )
        self.store.update_counterfactuals(
            {"AMD": 101.0}, now=observed + timedelta(minutes=31)
        )
        row = self.journal.connection.execute(
            "SELECT * FROM advanced_opportunities WHERE id=?", (row_id,)
        ).fetchone()
        self.assertAlmostEqual(row["outcome_5m_pct"], 1.0)
        self.assertAlmostEqual(row["outcome_15m_pct"], 1.0)
        self.assertAlmostEqual(row["outcome_30m_pct"], 1.0)
        self.assertIsNone(row["outcome_60m_pct"])
        self.store.update_counterfactuals(
            {"AMD": 99.0}, now=observed + timedelta(minutes=61)
        )
        row = self.journal.connection.execute(
            "SELECT * FROM advanced_opportunities WHERE id=?", (row_id,)
        ).fetchone()
        self.assertAlmostEqual(row["outcome_60m_pct"], -1.0)
        self.assertAlmostEqual(row["max_favorable_60m_pct"], 1.0)
        self.assertAlmostEqual(row["max_adverse_60m_pct"], -1.0)
        self.assertIsNotNone(row["resolved_at"])

    def test_calibration_uses_observed_history_after_minimum_sample(self):
        now = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
        for i in range(24):
            row_id = self.store.record_opportunity(
                symbol=f"S{i}",
                decision="ADVANCED_PASS",
                strategy="momentum",
                regime="bull_trend",
                price=100,
                score=80,
                probability=0.6,
                expected_value_pct=0.05,
                features={"signal_score": 80},
                now=now + timedelta(seconds=i * 100),
                dedupe_seconds=0,
            )
            outcome = 0.4 if i < 16 else -0.3
            self.journal.connection.execute(
                "UPDATE advanced_opportunities SET outcome_30m_pct=? WHERE id=?",
                (outcome, row_id),
            )
        self.journal.connection.commit()
        result = self.store.calibration("momentum", "bull_trend")
        self.assertEqual(result["sample"], 24)
        self.assertIsNotNone(result["probability"])
        self.assertGreater(result["probability"], 0.5)

    def test_router_can_nominate_non_dip_momentum_or_breakout(self):
        regime = MarketRegimeClassifier().classify(
            {"SPY": bars(), "QQQ": bars(start=200)}
        )
        signal = NS(
            score=80,
            dip_pct=-0.001,
            rsi=60,
            momentum_pct=0.003,
            volume_ratio=1.5,
            price_vs_vwap_pct=0.002,
        )
        item = NS(
            symbol="AMD",
            price=float(bars()["close"].iloc[-1]),
            signal=signal,
            entry_ready=False,
            reversal_confirmed=False,
        )
        routed = AdvancedOpportunityRouter(
            minimum_signal_score=60,
            minimum_strategy_score=65,
            top_n=3,
        ).route([item], {"AMD": bars()}, regime)
        self.assertEqual(len(routed), 1)
        self.assertIn(routed[0].strategy, {"momentum", "breakout", "trend_following"})
        self.assertFalse(item.entry_ready)

    def test_portfolio_model_blocks_high_correlation(self):
        candidate = bars()
        held = candidate.copy()
        held["close"] = held["close"] * 2.0
        model = PortfolioRiskModel(max_sector_positions=3, max_pair_correlation=0.90)
        allowed, reason, corr = model.assess(
            "AMD",
            {"NVDA": NS(market_value=20)},
            {"AMD": candidate, "NVDA": held},
        )
        self.assertFalse(allowed)
        self.assertGreaterEqual(corr, 0.90)
        self.assertIn("correlation", reason)

    def test_meta_engine_requires_positive_expectancy_and_portfolio_ok(self):
        engine = AdvancedDecisionEngine(
            self.store,
            take_profit_pct=0.0045,
            stop_loss_pct=0.005,
            minimum_strategy_score=60,
            minimum_probability=0.50,
        )
        regime = MarketRegimeClassifier().classify(
            {"SPY": bars(), "QQQ": bars(start=200)}
        )
        features = {
            "signal_score": 100,
            "dip_pct": 0.005,
            "rsi": 42,
            "momentum_pct": 0.004,
            "relative_volume": 1.6,
            "price_vs_vwap_pct": 0.003,
            "intraday_alignment": "bullish",
            "daily_trend": "uptrend",
            "latest_green": True,
            "room_to_resistance_pct": 0.2,
            "research_passed": True,
            "news_count": 2,
            "filing_count": 1,
        }
        decision = engine.evaluate(features, regime, (True, "portfolio ok", 0.2))
        self.assertTrue(decision.allowed, decision.reason)
        self.assertGreater(decision.probability, 0.5)
        self.assertGreater(decision.expected_value_pct, 0)

    def test_research_lab_promotes_only_to_shadow(self):
        now = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
        for i in range(35):
            row_id = self.store.record_opportunity(
                symbol=f"T{i}",
                decision="ADVANCED_PASS",
                strategy="momentum",
                regime="bull_trend",
                price=100,
                score=80,
                probability=0.6,
                expected_value_pct=0.05,
                features={"signal_score": 80},
                now=now + timedelta(seconds=i * 100),
                dedupe_seconds=0,
            )
            self.journal.connection.execute(
                "UPDATE advanced_opportunities SET outcome_30m_pct=? WHERE id=?",
                (0.30 if i < 25 else -0.10, row_id),
            )
        for i in range(35):
            row_id = self.store.record_opportunity(
                symbol=f"U{i}",
                decision="REJECT_PREFILTER",
                strategy="mean_reversion",
                regime="range",
                price=100,
                score=50,
                probability=0.45,
                expected_value_pct=-0.02,
                features={"signal_score": 50},
                now=now + timedelta(seconds=5000 + i * 100),
                dedupe_seconds=0,
            )
            self.journal.connection.execute(
                "UPDATE advanced_opportunities SET outcome_30m_pct=? WHERE id=?",
                (-0.15 if i < 25 else 0.05, row_id),
            )
        self.journal.connection.commit()

        report = ChampionChallengerLab(self.store, minimum_sample=30).run(now=now)
        statuses = {(x["strategy"], x["regime"]): x["status"] for x in report["candidates"]}
        self.assertEqual(statuses[("momentum", "bull_trend")], "SHADOW")
        self.assertNotEqual(statuses[("mean_reversion", "range")], "SHADOW")
        self.assertFalse(report["production_changes"])


if __name__ == "__main__":
    unittest.main()
