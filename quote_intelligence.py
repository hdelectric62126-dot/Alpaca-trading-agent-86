"""Batch Level-1 quote intelligence for execution-quality checks.

Uses Alpaca's latest best bid/ask quote endpoint through alpaca-py. The module
never submits orders. It validates quote freshness and computes spread cost in
basis points for the decision engine and slippage journal.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import math

from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockLatestQuoteRequest


@dataclass(frozen=True)
class QuoteMetrics:
    symbol: str
    available: bool
    reason: str
    bid: float | None
    ask: float | None
    mid: float | None
    spread_bps: float | None
    timestamp: datetime | None


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


class LevelOneQuoteAgent:
    def __init__(self, client, *, max_age_seconds=120, feed=DataFeed.IEX):
        self.client = client
        self.max_age_seconds = int(max_age_seconds)
        self.feed = feed

    def _validate(self, symbol, quote, now):
        if quote is None:
            return QuoteMetrics(symbol, False, "quote missing", None, None, None, None, None)

        bid = getattr(quote, "bid_price", None)
        ask = getattr(quote, "ask_price", None)
        timestamp = getattr(quote, "timestamp", None)
        if not _finite(bid) or not _finite(ask) or float(bid) <= 0 or float(ask) <= 0:
            return QuoteMetrics(symbol, False, "invalid bid/ask", None, None, None, None, timestamp)
        bid, ask = float(bid), float(ask)
        if ask < bid:
            return QuoteMetrics(symbol, False, "crossed quote", bid, ask, None, None, timestamp)
        mid = (bid + ask) / 2.0
        spread_bps = ((ask - bid) / mid) * 10000.0 if mid > 0 else None

        if timestamp is None:
            return QuoteMetrics(symbol, False, "quote timestamp missing", bid, ask, mid, spread_bps, None)
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
            return QuoteMetrics(symbol, False, "quote timestamp has no timezone", bid, ask, mid, spread_bps, None)
        timestamp = timestamp.astimezone(timezone.utc)
        age = (now - timestamp).total_seconds()
        if age < -5:
            return QuoteMetrics(symbol, False, "quote timestamp is in the future", bid, ask, mid, spread_bps, timestamp)
        if age > self.max_age_seconds:
            return QuoteMetrics(symbol, False, f"quote is stale by {age:.0f}s", bid, ask, mid, spread_bps, timestamp)
        return QuoteMetrics(symbol, True, "level-1 quote passed", bid, ask, mid, spread_bps, timestamp)

    def fetch(self, symbols, *, now=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        normalized = list(dict.fromkeys(
            str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()
        ))
        if not normalized:
            return {}

        request = StockLatestQuoteRequest(
            symbol_or_symbols=normalized,
            feed=self.feed,
        )
        try:
            quotes = self.client.get_stock_latest_quote(request)
        except Exception as exc:
            reason = "level-1 quote request failed: " + type(exc).__name__
            return {
                symbol: QuoteMetrics(symbol, False, reason, None, None, None, None, None)
                for symbol in normalized
            }

        return {
            symbol: self._validate(symbol, quotes.get(symbol), now)
            for symbol in normalized
        }
