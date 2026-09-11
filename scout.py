"""Market-scanning agent that ranks explainable mean-reversion setups."""

from dataclasses import dataclass

import pandas as pd

from strategy import Signal, score_signal


@dataclass(frozen=True)
class Opportunity:
    symbol: str
    price: float
    mean_price: float
    trigger_price: float
    signal: Signal

    @property
    def entry_ready(self) -> bool:
        return self.price <= self.trigger_price


class MarketScout:
    """Scores supplied market bars and returns the best opportunities first."""

    def __init__(self, dip_threshold: float, minimum_score: int, top_n: int = 3):
        if top_n < 1:
            raise ValueError("top_n must be at least 1")
        self.dip_threshold = float(dip_threshold)
        self.minimum_score = int(minimum_score)
        self.top_n = int(top_n)

    def analyze(self, symbol: str, bars: pd.DataFrame) -> Opportunity:
        closes = bars["close"].astype(float)
        price = float(closes.iloc[-1])
        mean_price = float(closes.mean())
        signal = score_signal(bars, self.dip_threshold)
        return Opportunity(
            symbol=symbol,
            price=price,
            mean_price=mean_price,
            trigger_price=mean_price * (1 - self.dip_threshold),
            signal=signal,
        )

    def rank(self, bars_by_symbol: dict[str, pd.DataFrame]) -> list[Opportunity]:
        opportunities = [
            self.analyze(symbol, bars)
            for symbol, bars in bars_by_symbol.items()
            if bars is not None and not bars.empty
        ]
        return sorted(
            opportunities,
            key=lambda item: (
                item.entry_ready,
                item.signal.score >= self.minimum_score,
                item.signal.score,
                item.signal.dip_pct,
                item.signal.volume_ratio,
            ),
            reverse=True,
        )

    def candidates(self, opportunities: list[Opportunity]) -> list[Opportunity]:
        eligible = [
            item for item in opportunities
            if item.entry_ready and item.signal.score >= self.minimum_score
        ]
        return eligible[:self.top_n]

