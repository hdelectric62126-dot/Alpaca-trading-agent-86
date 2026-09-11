"""Explainable signal scoring and a simple walk-forward backtester."""

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Signal:
    score: int
    reasons: tuple[str, ...]
    dip_pct: float
    rsi: float
    price_vs_vwap_pct: float
    volume_ratio: float
    momentum_pct: float
    component_scores: dict[str, int]


def _rsi(closes: pd.Series, period: int = 14) -> float:
    changes = closes.diff().dropna()
    gains = changes.clip(lower=0).rolling(period).mean().iloc[-1]
    losses = -changes.clip(upper=0).rolling(period).mean().iloc[-1]
    if pd.isna(gains) or pd.isna(losses):
        return 50.0
    if losses == 0:
        return 100.0
    return float(100 - (100 / (1 + gains / losses)))


def score_signal(bars: pd.DataFrame, dip_threshold: float = 0.0035) -> Signal:
    """Score the latest bar using only the supplied historical bars."""
    if len(bars) < 2:
        raise ValueError("At least two bars are required to score a signal")

    closes = bars["close"].astype(float)
    volumes = bars["volume"].astype(float)
    last_price = float(closes.iloc[-1])
    mean_price = float(closes.mean())
    dip_pct = (mean_price - last_price) / mean_price
    typical_price = (bars["high"] + bars["low"] + bars["close"]) / 3
    vwap = float((typical_price * volumes).sum() / volumes.sum())
    rsi = _rsi(closes)
    volume_average = float(volumes.iloc[:-1].tail(20).mean()) or 1.0
    volume_ratio = float(volumes.iloc[-1] / volume_average)
    momentum_pct = float((closes.iloc[-1] / closes.iloc[-min(5, len(closes))]) - 1)
    prior_momentum_pct = float(
        (closes.iloc[-min(5, len(closes) - 1)] / closes.iloc[-min(10, len(closes) - 1)]) - 1
    ) if len(closes) > 2 else 0.0

    points = 0
    reasons = []
    component_scores = {}
    if dip_pct >= dip_threshold:
        points += 20
        component_scores["dip"] = 20
        reasons.append(f"dip {dip_pct * 100:.2f}% meets {dip_threshold * 100:.2f}% target (+20)")
    else:
        component_scores["dip"] = 0
        reasons.append(f"dip {dip_pct * 100:.2f}% is below {dip_threshold * 100:.2f}% target (+0)")

    if 30 <= rsi <= 55:
        points += 20
        component_scores["rsi"] = 20
        reasons.append(f"RSI {rsi:.1f} is in a recovery range (+20)")
    else:
        component_scores["rsi"] = 0
        reasons.append(f"RSI {rsi:.1f} is outside the recovery range (+0)")

    price_vs_vwap_pct = (last_price / vwap) - 1
    if last_price >= vwap:
        points += 20
        component_scores["vwap"] = 20
        reasons.append(f"price is {price_vs_vwap_pct * 100:.2f}% above VWAP (+20)")
    else:
        component_scores["vwap"] = 0
        reasons.append(f"price is {abs(price_vs_vwap_pct) * 100:.2f}% below VWAP (+0)")

    if volume_ratio >= 1.2:
        points += 20
        component_scores["volume"] = 20
        reasons.append(f"volume is {volume_ratio:.2f}x its recent average (+20)")
    else:
        component_scores["volume"] = 0
        reasons.append(f"volume is {volume_ratio:.2f}x its recent average (+0)")

    if momentum_pct > prior_momentum_pct:
        points += 20
        component_scores["momentum"] = 20
        reasons.append("short-term momentum is improving (+20)")
    else:
        component_scores["momentum"] = 0
        reasons.append("short-term momentum is not improving (+0)")

    return Signal(
        score=points,
        reasons=tuple(reasons),
        dip_pct=dip_pct,
        rsi=rsi,
        price_vs_vwap_pct=price_vs_vwap_pct,
        volume_ratio=volume_ratio,
        momentum_pct=momentum_pct,
        component_scores=component_scores,
    )


def walk_forward_backtest(
    bars: pd.DataFrame,
    dip_threshold: float = 0.0035,
    minimum_score: int = 60,
    take_profit: float = 0.0045,
    stop_loss: float = 0.005,
    lookback: int = 30,
) -> dict:
    """Simulate sequential entries and exits without using future bars."""
    cash = 1_000.0
    entry_price = None
    trades = []
    for index in range(lookback, len(bars)):
        window = bars.iloc[index - lookback:index]
        price = float(bars["close"].iloc[index])
        if entry_price is not None:
            if price >= entry_price * (1 + take_profit) or price <= entry_price * (1 - stop_loss):
                change = (price / entry_price) - 1
                cash *= 1 + change
                trades.append(change)
                entry_price = None
            continue
        signal = score_signal(window, dip_threshold)
        if signal.score >= minimum_score:
            entry_price = price

    if entry_price is not None:
        change = (float(bars["close"].iloc[-1]) / entry_price) - 1
        cash *= 1 + change
        trades.append(change)

    wins = sum(change > 0 for change in trades)
    return {
        "starting_cash": 1000.0,
        "ending_cash": round(cash, 2),
        "return_pct": round((cash / 1000 - 1) * 100, 2),
        "trades": len(trades),
        "wins": wins,
        "win_rate_pct": round(wins / len(trades) * 100, 2) if trades else 0.0,
    }