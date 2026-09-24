"""Deterministic pre-trade quality and market-regime gates for paper entries."""

from dataclasses import dataclass
import math

import pandas as pd


@dataclass(frozen=True)
class TechnicalPlan:
    allowed: bool
    reason: str
    price: float
    vwap: float
    volume_ratio: float
    atr_pct: float
    resistance: float
    reward_risk: float
    latest_green: bool
    vwap_reclaimed: bool
    recovery_trend_up: bool


@dataclass(frozen=True)
class MarketRegime:
    allowed: bool
    reason: str
    weak_benchmarks: tuple[str, ...]


def _finite_positive(value):
    return math.isfinite(float(value)) and float(value) > 0


def build_technical_plan(
    bars: pd.DataFrame,
    *,
    take_profit_pct: float,
    stop_loss_pct: float,
    min_volume_ratio: float = 1.0,
    min_reward_risk: float = 1.0,
    max_atr_pct: float = 0.02,
) -> TechnicalPlan:
    """Require evidence of a real reversal with room for the trade to work."""
    if len(bars) < 20:
        raise ValueError("At least 20 completed bars are required")
    if take_profit_pct <= 0 or stop_loss_pct <= 0:
        raise ValueError("Profit and stop percentages must be positive")
    if min_volume_ratio <= 0 or min_reward_risk <= 0 or max_atr_pct <= 0:
        raise ValueError("Gate thresholds must be positive")

    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(bars.columns):
        raise ValueError("OHLCV bars are required")

    data = bars[["open", "high", "low", "close", "volume"]].astype(float)
    if not all(math.isfinite(v) for v in data.to_numpy().ravel()):
        raise ValueError("Bars must be finite")

    closes = data["close"]
    volumes = data["volume"]
    highs = data["high"]
    price = float(closes.iloc[-1])
    if not _finite_positive(price) or volumes.sum() <= 0:
        raise ValueError("Bars must contain positive price and usable volume")

    typical = (data["high"] + data["low"] + data["close"]) / 3.0
    vwap = float((typical * volumes).sum() / volumes.sum())
    prior_volume = float(volumes.iloc[:-1].tail(20).mean())
    volume_ratio = float(volumes.iloc[-1] / prior_volume) if prior_volume > 0 else 0.0

    previous_close = closes.shift(1)
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - previous_close).abs(),
            (data["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = float(true_range.tail(14).mean())
    atr_pct = atr / price

    latest_green = float(data["close"].iloc[-1]) > float(data["open"].iloc[-1])
    vwap_reclaimed = price >= vwap
    recovery_trend_up = float(closes.tail(5).mean()) > float(closes.iloc[-10:-5].mean())

    prior_highs = highs.iloc[-20:-1]
    resistance = float(prior_highs.max()) if not prior_highs.empty else price
    room_to_resistance = max(0.0, (resistance - price) / price)
    target_room = min(room_to_resistance, take_profit_pct * 1.5)
    reward_risk = target_room / stop_loss_pct

    failures = []
    if not latest_green:
        failures.append("latest completed bar is not green")
    if not vwap_reclaimed:
        failures.append("price has not reclaimed VWAP")
    if not recovery_trend_up:
        failures.append("5-bar recovery trend is not improving")
    if volume_ratio < min_volume_ratio:
        failures.append(f"relative volume {volume_ratio:.2f}x is below {min_volume_ratio:.2f}x")
    if atr_pct > max_atr_pct:
        failures.append(f"ATR {atr_pct * 100:.2f}% exceeds {max_atr_pct * 100:.2f}%")
    if reward_risk < min_reward_risk:
        failures.append(f"reward/risk {reward_risk:.2f} is below {min_reward_risk:.2f}")

    return TechnicalPlan(
        allowed=not failures,
        reason="; ".join(failures) if failures else "technical quality gate passed",
        price=price,
        vwap=vwap,
        volume_ratio=volume_ratio,
        atr_pct=atr_pct,
        resistance=resistance,
        reward_risk=reward_risk,
        latest_green=latest_green,
        vwap_reclaimed=vwap_reclaimed,
        recovery_trend_up=recovery_trend_up,
    )


def assess_market_regime(
    bars_by_symbol: dict[str, pd.DataFrame],
    benchmarks=("SPY", "QQQ"),
) -> MarketRegime:
    """Block new long entries when all usable broad-market benchmarks weaken."""
    weak = []
    usable = 0
    for symbol in benchmarks:
        bars = bars_by_symbol.get(symbol)
        if bars is None or len(bars) < 10:
            continue
        closes = bars["close"].astype(float)
        usable += 1
        latest = float(closes.iloc[-1])
        recent = float(closes.tail(5).mean())
        prior = float(closes.iloc[-10:-5].mean())
        if latest < recent and recent < prior:
            weak.append(symbol)

    if usable == 0:
        return MarketRegime(False, "benchmark data unavailable", tuple())
    if len(weak) == usable:
        return MarketRegime(False, "broad market benchmarks are weakening", tuple(weak))
    return MarketRegime(True, "market regime permits selective long entries", tuple(weak))
