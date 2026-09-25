"""Advanced, paper-only decision architecture.

This module adds a persistent feature store, counterfactual tracking, market
regime classification, strategy-ensemble scoring, probability calibration,
portfolio correlation controls, and champion/challenger research. It never
imports a broker client and cannot submit orders.

Design rule: models may propose and rank; deterministic risk/guardian controls
retain final authority.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import statistics

import pandas as pd


SECTOR_MAP = {
    "AMD": "semiconductors",
    "NVDA": "semiconductors",
    "TSM": "semiconductors",
    "AVGO": "semiconductors",
    "AAPL": "technology",
    "MSFT": "technology",
    "META": "communication",
    "GOOGL": "communication",
    "AMZN": "consumer",
    "TSLA": "consumer",
    "JPM": "financials",
    "BAC": "financials",
    "XOM": "energy",
    "CVX": "energy",
    "LLY": "healthcare",
    "UNH": "healthcare",
    "CAT": "industrials",
    "GE": "industrials",
    "WMT": "consumer_staples",
    "COST": "consumer_staples",
    "SPY": "benchmark",
    "QQQ": "benchmark",
}


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _utc_now(now=None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("time must be timezone-aware")
    return now.astimezone(timezone.utc)


def _mean(values):
    values = [float(x) for x in values if x is not None and _finite(x)]
    return statistics.fmean(values) if values else None


@dataclass(frozen=True)
class RegimeSnapshot:
    name: str
    trend: str
    volatility: str
    breadth: float
    benchmark_momentum_pct: float
    atr_pct: float
    usable_benchmarks: int


@dataclass(frozen=True)
class StrategyVote:
    name: str
    score: float
    reason: str


@dataclass(frozen=True)
class MetaDecision:
    allowed: bool
    strategy: str
    regime: str
    probability: float
    expected_value_pct: float
    calibration_sample: int
    portfolio_reason: str
    reason: str
    votes: tuple[StrategyVote, ...]


class AdvancedFeatureStore:
    """SQLite-backed observation store using the existing journal connection."""

    HORIZONS = (5, 15, 30, 60)

    def __init__(self, connection):
        self.db = connection
        self._create_tables()

    def _create_tables(self):
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS advanced_opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                decision TEXT NOT NULL,
                strategy TEXT NOT NULL,
                regime TEXT NOT NULL,
                price REAL NOT NULL,
                score REAL NOT NULL,
                probability REAL NOT NULL,
                expected_value_pct REAL NOT NULL,
                features_json TEXT NOT NULL,
                reason TEXT,
                outcome_5m_pct REAL,
                outcome_15m_pct REAL,
                outcome_30m_pct REAL,
                outcome_60m_pct REAL,
                max_favorable_60m_pct REAL,
                max_adverse_60m_pct REAL,
                resolved_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_advanced_opportunities_time
                ON advanced_opportunities(observed_at);
            CREATE INDEX IF NOT EXISTS idx_advanced_opportunities_symbol
                ON advanced_opportunities(symbol, observed_at);

            CREATE TABLE IF NOT EXISTS champion_challengers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                strategy TEXT NOT NULL,
                regime TEXT NOT NULL,
                sample_size INTEGER NOT NULL,
                win_rate_pct REAL NOT NULL,
                expectancy_pct REAL NOT NULL,
                baseline_expectancy_pct REAL NOT NULL,
                status TEXT NOT NULL,
                parameters_json TEXT NOT NULL,
                reason TEXT NOT NULL
            );
            """
        )
        self.db.commit()

    def record_opportunity(
        self,
        *,
        symbol,
        decision,
        strategy,
        regime,
        price,
        score,
        probability,
        expected_value_pct,
        features,
        reason="",
        now=None,
        dedupe_seconds=90,
    ):
        now = _utc_now(now)
        symbol = str(symbol).strip().upper()
        if not symbol or not _finite(price) or float(price) <= 0:
            raise ValueError("valid symbol and price required")
        cutoff = (now - timedelta(seconds=int(dedupe_seconds))).isoformat()
        existing = self.db.execute(
            """SELECT id FROM advanced_opportunities
               WHERE symbol=? AND decision=? AND strategy=? AND observed_at>=?
               ORDER BY id DESC LIMIT 1""",
            (symbol, str(decision), str(strategy), cutoff),
        ).fetchone()
        if existing:
            return int(existing["id"])

        payload = json.dumps(features, sort_keys=True, allow_nan=False)
        with self.db:
            cur = self.db.execute(
                """INSERT INTO advanced_opportunities
                   (observed_at,symbol,decision,strategy,regime,price,score,
                    probability,expected_value_pct,features_json,reason,
                    max_favorable_60m_pct,max_adverse_60m_pct)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    now.isoformat(), symbol, str(decision), str(strategy), str(regime),
                    float(price), float(score), float(probability),
                    float(expected_value_pct), payload, str(reason)[:2000], 0.0, 0.0,
                ),
            )
        return int(cur.lastrowid)

    def update_counterfactuals(self, current_prices: dict[str, float], *, now=None):
        now = _utc_now(now)
        if not current_prices:
            return 0
        rows = self.db.execute(
            """SELECT * FROM advanced_opportunities
               WHERE resolved_at IS NULL
               ORDER BY observed_at"""
        ).fetchall()
        updated = 0
        with self.db:
            for row in rows:
                symbol = row["symbol"]
                value = current_prices.get(symbol)
                if not _finite(value) or float(value) <= 0:
                    continue
                observed = datetime.fromisoformat(row["observed_at"]).astimezone(timezone.utc)
                elapsed = (now - observed).total_seconds() / 60.0
                if elapsed < 0:
                    continue
                price = float(value)
                entry = float(row["price"])
                move_pct = (price / entry - 1.0) * 100.0

                favorable = max(float(row["max_favorable_60m_pct"] or 0.0), move_pct)
                adverse = min(float(row["max_adverse_60m_pct"] or 0.0), move_pct)
                updates = {
                    "max_favorable_60m_pct": favorable,
                    "max_adverse_60m_pct": adverse,
                }
                for horizon in self.HORIZONS:
                    field = f"outcome_{horizon}m_pct"
                    if elapsed >= horizon and row[field] is None:
                        updates[field] = move_pct
                if elapsed >= 60:
                    updates["resolved_at"] = now.isoformat()

                assignments = ", ".join(f"{key}=?" for key in updates)
                values = list(updates.values()) + [row["id"]]
                self.db.execute(
                    f"UPDATE advanced_opportunities SET {assignments} WHERE id=?",
                    values,
                )
                updated += 1
        return updated

    def calibration(self, strategy, regime, *, horizon=30, minimum=20):
        if horizon not in self.HORIZONS:
            raise ValueError("unsupported calibration horizon")
        field = f"outcome_{horizon}m_pct"
        rows = self.db.execute(
            f"""SELECT {field} AS outcome FROM advanced_opportunities
                WHERE strategy=? AND regime=? AND {field} IS NOT NULL
                ORDER BY observed_at DESC LIMIT 500""",
            (strategy, regime),
        ).fetchall()
        outcomes = [float(row["outcome"]) for row in rows if _finite(row["outcome"])]
        if len(outcomes) < minimum:
            return {"sample": len(outcomes), "probability": None, "expectancy_pct": None}
        # Treat +0.05% as a small friction buffer rather than labeling every
        # microscopic positive move a success.
        wins = sum(value > 0.05 for value in outcomes)
        prior_strength = 12.0
        probability = (wins + prior_strength * 0.5) / (len(outcomes) + prior_strength)
        return {
            "sample": len(outcomes),
            "probability": probability,
            "expectancy_pct": statistics.fmean(outcomes),
        }

    def estimated_round_trip_cost_pct(self):
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(paper_trades)")}
        if not {"entry_slippage_bps", "exit_slippage_bps"}.issubset(columns):
            return 0.04
        rows = self.db.execute(
            """SELECT entry_slippage_bps, exit_slippage_bps FROM paper_trades
               WHERE status='CLOSED' AND fill_verified=1
               ORDER BY closed_at DESC LIMIT 100"""
        ).fetchall()
        costs = []
        for row in rows:
            entry = row["entry_slippage_bps"]
            exit_ = row["exit_slippage_bps"]
            if _finite(entry) and _finite(exit_):
                costs.append(max(0.0, float(entry)) + max(0.0, float(exit_)))
        if not costs:
            return 0.04
        return min(1.0, statistics.fmean(costs) / 100.0)

    def rolling_trade_health(self, minimum=20, limit=50):
        rows = self.db.execute(
            """SELECT realized_pnl FROM paper_trades
               WHERE status='CLOSED' AND fill_verified=1 AND realized_pnl IS NOT NULL
               ORDER BY closed_at DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
        values = [float(row["realized_pnl"]) for row in rows if _finite(row["realized_pnl"])]
        return {
            "sample": len(values),
            "expectancy": statistics.fmean(values) if values else None,
            "negative": len(values) >= minimum and statistics.fmean(values) <= 0,
        }


class MarketRegimeClassifier:
    def classify(self, bars_by_symbol, benchmarks=("SPY", "QQQ")):
        votes = []
        momentums = []
        atrs = []
        for symbol in benchmarks:
            bars = bars_by_symbol.get(symbol)
            if bars is None or len(bars) < 20:
                continue
            closes = bars["close"].astype(float)
            highs = bars["high"].astype(float)
            lows = bars["low"].astype(float)
            latest = float(closes.iloc[-1])
            fast = float(closes.tail(5).mean())
            slow = float(closes.tail(20).mean())
            momentum = (latest / float(closes.iloc[-10]) - 1.0) * 100.0
            previous = closes.shift(1)
            tr = pd.concat(
                [
                    highs - lows,
                    (highs - previous).abs(),
                    (lows - previous).abs(),
                ],
                axis=1,
            ).max(axis=1)
            atr_pct = float(tr.tail(14).mean() / latest)
            bullish = latest > fast > slow
            bearish = latest < fast < slow
            votes.append(1 if bullish else (-1 if bearish else 0))
            momentums.append(momentum)
            atrs.append(atr_pct)

        if not votes:
            return RegimeSnapshot("unknown", "unknown", "unknown", 0.0, 0.0, 0.0, 0)

        breadth = sum(v > 0 for v in votes) / len(votes)
        avg_momentum = statistics.fmean(momentums)
        avg_atr = statistics.fmean(atrs)
        if all(v > 0 for v in votes):
            trend = "bullish"
        elif all(v < 0 for v in votes):
            trend = "bearish"
        else:
            trend = "mixed"

        volatility = "high" if avg_atr >= 0.0030 else ("low" if avg_atr <= 0.0010 else "normal")
        if trend == "bullish":
            name = "bull_trend_high_vol" if volatility == "high" else "bull_trend"
        elif trend == "bearish":
            name = "bear_trend_high_vol" if volatility == "high" else "bear_trend"
        else:
            name = "range_high_vol" if volatility == "high" else "range"

        return RegimeSnapshot(
            name=name,
            trend=trend,
            volatility=volatility,
            breadth=breadth,
            benchmark_momentum_pct=avg_momentum,
            atr_pct=avg_atr,
            usable_benchmarks=len(votes),
        )


class StrategyEnsemble:
    """Several transparent strategy specialists competing for each setup."""

    def vote(self, features):
        score = float(features.get("signal_score", 0.0))
        dip = float(features.get("dip_pct", 0.0))
        rsi = float(features.get("rsi", 50.0))
        momentum = float(features.get("momentum_pct", 0.0))
        rvol = float(features.get("relative_volume", 0.0) or 0.0)
        above_vwap = float(features.get("price_vs_vwap_pct", 0.0)) >= 0
        intraday = str(features.get("intraday_alignment", "unknown"))
        daily = str(features.get("daily_trend", "unknown"))
        latest_green = bool(features.get("latest_green", False))
        near_resistance = float(features.get("room_to_resistance_pct", 99.0)) <= 0.35

        dip_reversal = min(
            100.0,
            score
            + (10 if dip >= 0.0035 else 0)
            + (10 if 30 <= rsi <= 55 else -8)
            + (8 if latest_green else -10),
        )
        momentum_score = max(
            0.0,
            min(
                100.0,
                45
                + min(25, max(-20, momentum * 5000))
                + (15 if rvol >= 1.2 else -10)
                + (10 if above_vwap else -15)
                + (10 if intraday == "bullish" else 0),
            ),
        )
        breakout = max(
            0.0,
            min(
                100.0,
                40
                + (20 if near_resistance else 0)
                + (20 if rvol >= 1.3 else 0)
                + (10 if latest_green else -10)
                + (10 if momentum > 0 else -10),
            ),
        )
        mean_reversion = max(
            0.0,
            min(
                100.0,
                45
                + (20 if dip >= 0.0035 else -10)
                + (15 if rsi <= 45 else -5)
                + (10 if latest_green else -10)
                + (10 if intraday != "bearish" else -20),
            ),
        )
        trend_following = max(
            0.0,
            min(
                100.0,
                40
                + (20 if intraday == "bullish" else -15)
                + (15 if daily == "uptrend" else -10)
                + (15 if above_vwap else -10)
                + (10 if momentum > 0 else -5),
            ),
        )
        research_state = features.get("research_passed")
        research_adjustment = 10 if research_state is True else (-25 if research_state is False else 0)
        catalyst = max(
            0.0,
            min(
                100.0,
                50
                + min(40, int(features.get("news_count", 0)) * 18)
                + min(15, int(features.get("filing_count", 0)) * 5)
                + research_adjustment
                - (40 if features.get("catalyst_risky") else 0),
            ),
        )

        votes = (
            StrategyVote("dip_reversal", dip_reversal, "dip + recovery + signal quality"),
            StrategyVote("momentum", momentum_score, "momentum + RVOL + VWAP alignment"),
            StrategyVote("breakout", breakout, "resistance proximity + participation"),
            StrategyVote("mean_reversion", mean_reversion, "deviation + RSI + reversal"),
            StrategyVote("trend_following", trend_following, "intraday/daily trend alignment"),
            StrategyVote("catalyst", catalyst, "news/filing catalyst evidence"),
        )
        return tuple(sorted(votes, key=lambda x: (-x.score, x.name)))


@dataclass(frozen=True)
class RoutedCandidate:
    opportunity: object
    strategy: str
    strategy_score: float
    reason: str


class AdvancedOpportunityRouter:
    """Allow independent long-strategy specialists to nominate candidates.

    The legacy dip scout still supplies normalized signal features, but a symbol
    no longer has to be a dip/reversal setup to reach the quality gates.
    """

    def __init__(self, *, minimum_signal_score=60, minimum_strategy_score=65, top_n=5):
        self.minimum_signal_score = int(minimum_signal_score)
        self.minimum_strategy_score = float(minimum_strategy_score)
        self.top_n = max(1, int(top_n))
        self.ensemble = StrategyEnsemble()

    def route(self, opportunities, bars_by_symbol, regime, catalysts=None):
        catalysts = catalysts or {}
        routed = []
        for item in opportunities:
            bars = bars_by_symbol.get(item.symbol)
            if bars is None or len(bars) < 20:
                continue
            closes = bars["close"].astype(float)
            latest = float(closes.iloc[-1])
            latest_green = float(bars["close"].iloc[-1]) > float(bars["open"].iloc[-1])
            prior_high = float(bars["high"].astype(float).iloc[-20:-1].max())
            features = build_feature_vector(item, regime=regime)
            catalyst = catalysts.get(item.symbol, {})
            features.update(
                {
                    "latest_green": latest_green,
                    "room_to_resistance_pct": max(0.0, (prior_high / latest - 1.0) * 100.0),
                    "news_count": int(catalyst.get("count", 0) or 0),
                    "catalyst_risky": int(catalyst.get("risky_count", 0) or 0) > 0,
                }
            )
            votes = self.ensemble.vote(features)
            winner = votes[0]
            signal_ok = item.signal.score >= self.minimum_signal_score
            strategy_ok = winner.score >= self.minimum_strategy_score
            above_vwap = item.signal.price_vs_vwap_pct >= 0
            positive_momentum = item.signal.momentum_pct > 0
            rvol = item.signal.volume_ratio
            above_mean = latest >= float(closes.tail(20).mean())
            near_breakout = latest >= prior_high * 0.999

            ready = False
            reason = ""
            if winner.name in {"dip_reversal", "mean_reversion"}:
                ready = item.entry_ready and signal_ok and strategy_ok
                reason = "reversal specialist nominated setup"
            elif winner.name == "momentum":
                ready = (
                    signal_ok and strategy_ok and positive_momentum
                    and above_vwap and rvol >= 1.2 and latest_green
                )
                reason = "momentum specialist nominated setup"
            elif winner.name == "breakout":
                ready = (
                    signal_ok and strategy_ok and near_breakout
                    and rvol >= 1.2 and latest_green
                )
                reason = "breakout specialist nominated setup"
            elif winner.name == "trend_following":
                ready = (
                    signal_ok and strategy_ok and above_mean
                    and above_vwap and positive_momentum and latest_green
                )
                reason = "trend specialist nominated setup"
            elif winner.name == "catalyst":
                news_count = int(features.get("news_count", 0) or 0)
                catalyst_risky = bool(features.get("catalyst_risky", False))
                ready = (
                    news_count > 0
                    and not catalyst_risky
                    and item.signal.score >= max(40, self.minimum_signal_score - 20)
                    and strategy_ok
                    and above_vwap
                    and latest_green
                    and rvol >= 1.0
                )
                reason = "catalyst specialist nominated setup from fresh watchlist news"
            else:
                reason = "no specialist nominated setup"

            if ready:
                routed.append(
                    RoutedCandidate(
                        opportunity=item,
                        strategy=winner.name,
                        strategy_score=winner.score,
                        reason=reason,
                    )
                )

        routed.sort(
            key=lambda candidate: (
                candidate.strategy_score,
                candidate.opportunity.signal.score,
                candidate.opportunity.signal.volume_ratio,
            ),
            reverse=True,
        )
        return routed[: self.top_n]


def strategy_quality_decision(
    strategy,
    item,
    plan,
    *,
    min_volume_ratio=1.0,
    max_atr_pct=0.02,
):
    """Apply quality rules suited to the strategy instead of one universal gate."""
    if strategy in {"dip_reversal", "mean_reversion"}:
        return bool(plan.allowed), plan.reason

    common_failures = []
    if not plan.latest_green:
        common_failures.append("latest completed bar is not green")
    if not plan.vwap_reclaimed:
        common_failures.append("price has not reclaimed VWAP")
    if not plan.recovery_trend_up:
        common_failures.append("recovery trend is not improving")
    if plan.volume_ratio < min_volume_ratio:
        common_failures.append(
            f"relative volume {plan.volume_ratio:.2f}x is below {min_volume_ratio:.2f}x"
        )
    if plan.atr_pct > max_atr_pct:
        common_failures.append(
            f"ATR {plan.atr_pct * 100:.2f}% exceeds {max_atr_pct * 100:.2f}%"
        )

    if strategy == "breakout":
        if float(item.price) < float(plan.resistance) * 0.995:
            common_failures.append("price has not reached the breakout zone")
    elif strategy in {"momentum", "trend_following", "catalyst"}:
        if plan.reward_risk < 0.50:
            common_failures.append(
                f"reward/risk {plan.reward_risk:.2f} is below strategy floor 0.50"
            )

    return (
        not common_failures,
        "strategy-aware quality gate passed"
        if not common_failures
        else "; ".join(common_failures),
    )


class PortfolioRiskModel:
    def __init__(self, *, max_sector_positions=2, max_pair_correlation=0.90):
        self.max_sector_positions = int(max_sector_positions)
        self.max_pair_correlation = float(max_pair_correlation)

    @staticmethod
    def _returns(bars):
        if bars is None or len(bars) < 15:
            return None
        return bars["close"].astype(float).pct_change().dropna()

    def assess(self, symbol, positions, bars_by_symbol):
        sector = SECTOR_MAP.get(symbol, "other")
        same_sector = [
            held for held in positions
            if SECTOR_MAP.get(held, "other") == sector
        ]
        if sector != "other" and len(same_sector) >= self.max_sector_positions:
            return False, f"sector concentration limit reached for {sector}", None

        candidate_returns = self._returns(bars_by_symbol.get(symbol))
        worst = None
        worst_symbol = None
        if candidate_returns is not None:
            for held in positions:
                held_returns = self._returns(bars_by_symbol.get(held))
                if held_returns is None:
                    continue
                aligned = pd.concat(
                    [candidate_returns.rename("candidate"), held_returns.rename("held")],
                    axis=1,
                    join="inner",
                ).dropna()
                if len(aligned) < 12:
                    continue
                corr = float(aligned["candidate"].corr(aligned["held"]))
                if _finite(corr) and (worst is None or corr > worst):
                    worst, worst_symbol = corr, held
        if worst is not None and worst >= self.max_pair_correlation:
            return False, f"correlation {worst:.2f} with open {worst_symbol} exceeds limit", worst
        reason = f"portfolio concentration acceptable; sector={sector}"
        if worst is not None:
            reason += f"; max_corr={worst:.2f}"
        return True, reason, worst


class AdvancedDecisionEngine:
    def __init__(
        self,
        store,
        *,
        take_profit_pct,
        stop_loss_pct,
        minimum_strategy_score=60.0,
        minimum_probability=0.50,
        minimum_expected_value_pct=0.0,
    ):
        self.store = store
        self.take_profit_pct = float(take_profit_pct)
        self.stop_loss_pct = float(stop_loss_pct)
        self.minimum_strategy_score = float(minimum_strategy_score)
        self.minimum_probability = float(minimum_probability)
        self.minimum_expected_value_pct = float(minimum_expected_value_pct)
        self.ensemble = StrategyEnsemble()

    def evaluate(self, features, regime, portfolio_assessment):
        votes = self.ensemble.vote(features)
        winner = votes[0]
        calibration = self.store.calibration(winner.name, regime.name)

        # Conservative prior from deterministic strategy quality. Historical
        # calibration replaces it only after enough observations exist.
        base_probability = max(0.38, min(0.68, 0.38 + winner.score / 100.0 * 0.30))
        probability = (
            float(calibration["probability"])
            if calibration["probability"] is not None
            else base_probability
        )
        cost_pct = self.store.estimated_round_trip_cost_pct()
        expected_value_pct = (
            probability * self.take_profit_pct * 100.0
            - (1.0 - probability) * self.stop_loss_pct * 100.0
            - cost_pct
        )

        portfolio_ok, portfolio_reason, _ = portfolio_assessment
        failures = []
        if winner.score < self.minimum_strategy_score:
            failures.append(f"best strategy score {winner.score:.1f} below {self.minimum_strategy_score:.1f}")
        if probability < self.minimum_probability:
            failures.append(f"estimated probability {probability:.3f} below {self.minimum_probability:.3f}")
        if expected_value_pct <= self.minimum_expected_value_pct:
            failures.append(f"expected value {expected_value_pct:.3f}% is not positive after costs")
        if not portfolio_ok:
            failures.append(portfolio_reason)

        health = self.store.rolling_trade_health()
        if health["negative"]:
            failures.append(
                f"rolling live-paper expectancy is non-positive across {health['sample']} closed trades"
            )

        return MetaDecision(
            allowed=not failures,
            strategy=winner.name,
            regime=regime.name,
            probability=probability,
            expected_value_pct=expected_value_pct,
            calibration_sample=int(calibration["sample"]),
            portfolio_reason=portfolio_reason,
            reason="advanced meta-decision passed" if not failures else "; ".join(failures),
            votes=votes,
        )


class ChampionChallengerLab:
    """Automatic nightly research that can promote only to SHADOW, never production."""

    def __init__(self, store, *, minimum_sample=30):
        self.store = store
        self.minimum_sample = max(10, int(minimum_sample))

    def run(self, *, now=None):
        now = _utc_now(now)
        rows = self.store.db.execute(
            """SELECT strategy, regime, outcome_30m_pct
               FROM advanced_opportunities
               WHERE outcome_30m_pct IS NOT NULL
               ORDER BY observed_at DESC LIMIT 5000"""
        ).fetchall()
        all_outcomes = [
            float(row["outcome_30m_pct"])
            for row in rows
            if _finite(row["outcome_30m_pct"])
        ]
        baseline = statistics.fmean(all_outcomes) if all_outcomes else 0.0

        grouped = {}
        for row in rows:
            outcome = row["outcome_30m_pct"]
            if not _finite(outcome):
                continue
            grouped.setdefault((row["strategy"], row["regime"]), []).append(float(outcome))

        candidates = []
        with self.store.db:
            for (strategy, regime), values in sorted(grouped.items()):
                sample = len(values)
                if sample < self.minimum_sample:
                    continue
                expectancy = statistics.fmean(values)
                win_rate = sum(v > 0.05 for v in values) / sample * 100.0
                qualifies = (
                    sample >= self.minimum_sample
                    and expectancy > baseline + 0.05
                    and expectancy > 0
                    and win_rate >= 52.0
                )
                status = "SHADOW" if qualifies else "RESEARCH"
                parameters = {
                    "strategy": strategy,
                    "regime": regime,
                    "minimum_observations": sample,
                }
                reason = (
                    "qualified for shadow evaluation; production rules unchanged"
                    if qualifies
                    else "evidence does not yet beat the global baseline strongly enough"
                )
                self.store.db.execute(
                    """INSERT INTO champion_challengers
                       (generated_at,strategy,regime,sample_size,win_rate_pct,
                        expectancy_pct,baseline_expectancy_pct,status,
                        parameters_json,reason)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        now.isoformat(), strategy, regime, sample, win_rate,
                        expectancy, baseline, status,
                        json.dumps(parameters, sort_keys=True), reason,
                    ),
                )
                candidates.append(
                    {
                        "strategy": strategy,
                        "regime": regime,
                        "sample": sample,
                        "win_rate_pct": round(win_rate, 2),
                        "expectancy_pct": round(expectancy, 4),
                        "baseline_expectancy_pct": round(baseline, 4),
                        "status": status,
                        "reason": reason,
                    }
                )
        return {
            "generated_at": now.isoformat(),
            "mode": "AUTOMATIC_RESEARCH_SHADOW_ONLY",
            "production_changes": False,
            "observations": len(all_outcomes),
            "baseline_expectancy_pct": round(baseline, 4),
            "candidates": candidates,
        }

    @staticmethod
    def log_summary(report):
        shadow = [x for x in report["candidates"] if x["status"] == "SHADOW"]
        print(
            f"[RESEARCH LAB] observations={report['observations']} "
            f"baseline_30m={report['baseline_expectancy_pct']:.4f}% "
            f"shadow_candidates={len(shadow)}"
        )
        for item in shadow[:5]:
            print(
                f"[CHALLENGER] {item['strategy']} regime={item['regime']} "
                f"n={item['sample']} win={item['win_rate_pct']:.1f}% "
                f"expectancy={item['expectancy_pct']:.4f}% status=SHADOW"
            )


def build_feature_vector(
    item,
    *,
    plan=None,
    intraday=None,
    intelligence=None,
    research=None,
    regime=None,
):
    features = {
        "signal_score": float(item.signal.score),
        "dip_pct": float(item.signal.dip_pct),
        "rsi": float(item.signal.rsi),
        "price_vs_vwap_pct": float(item.signal.price_vs_vwap_pct),
        "relative_volume": float(item.signal.volume_ratio),
        "momentum_pct": float(item.signal.momentum_pct),
        "reversal_confirmed": bool(item.reversal_confirmed),
    }
    if plan is not None:
        room = max(0.0, (float(plan.resistance) / float(item.price) - 1.0) * 100.0)
        features.update(
            {
                "quality_gate_passed": bool(plan.allowed),
                "technical_reward_risk": float(plan.reward_risk),
                "atr_pct": float(plan.atr_pct),
                "latest_green": bool(plan.latest_green),
                "vwap_reclaimed": bool(plan.vwap_reclaimed),
                "room_to_resistance_pct": room,
            }
        )
    if intraday is not None:
        features.update(
            {
                "intraday_alignment": intraday.alignment,
                "intraday_vwap": intraday.vwap,
                "intraday_rvol": intraday.relative_volume,
                "five_minute_trend": intraday.five_minute.trend if intraday.five_minute else "unknown",
                "fifteen_minute_trend": intraday.fifteen_minute.trend if intraday.fifteen_minute else "unknown",
            }
        )
    if intelligence is not None:
        features.update(
            {
                "daily_trend": intelligence.technical.trend,
                "daily_rsi": intelligence.technical.rsi,
                "daily_macd_state": intelligence.technical.macd_state,
                "fundamental_match_confidence_pct": intelligence.fundamentals.match_confidence_pct,
                "pe_ratio": intelligence.fundamentals.pe_ratio,
                "revenue_growth_pct": intelligence.fundamentals.revenue_growth_pct,
            }
        )
    if research is not None:
        features.update(
            {
                "research_passed": bool(research.allowed),
                "news_count": len(research.news_headlines),
                "filing_count": len(research.recent_filings),
            }
        )
    if regime is not None:
        features.update(
            {
                "market_regime": regime.name,
                "market_trend": regime.trend,
                "market_volatility": regime.volatility,
                "market_breadth": regime.breadth,
                "benchmark_momentum_pct": regime.benchmark_momentum_pct,
            }
        )
    # SQLite JSON is strict; convert NaN/Inf to null.
    for key, value in list(features.items()):
        if isinstance(value, float) and not math.isfinite(value):
            features[key] = None
    return features
