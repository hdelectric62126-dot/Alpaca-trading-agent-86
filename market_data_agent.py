"""Fetch the watchlist together and isolate unusable data by symbol."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import time

import pandas as pd
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

@dataclass
class MarketSnapshot:
    bars: dict = field(default_factory=dict)
    rejected: dict = field(default_factory=dict)
    request_count: int = 0
    elapsed_seconds: float = 0.0
    transport_ok: bool = True


class MarketDataAgent:
    def __init__(self, client, *, lookback=30, max_age_seconds=180):
        if lookback < 2 or max_age_seconds <= 0:
            raise ValueError('Market data limits must be positive; lookback at least 2')
        self.client = client
        self.lookback = lookback
        self.max_age_seconds = max_age_seconds

    def clean(self, bars, symbol, now, *, minimum_bars):
        if bars is None or bars.empty:
            raise ValueError('no bars returned')
        if isinstance(bars.index, pd.MultiIndex):
            try:
                bars = bars.xs(symbol, level=0)
            except KeyError as exc:
                raise ValueError('symbol missing from response') from exc
        if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
            raise ValueError('timestamps must have timezones')
        bars = bars.sort_index()
        bars = bars[~bars.index.duplicated(keep='last')]
        eastern = bars.index.tz_convert('America/New_York')
        current = pd.Timestamp(now)
        if current.tzinfo is None:
            raise ValueError('current time must have a timezone')
        minute = eastern.hour * 60 + eastern.minute
        mask = ((eastern.date == current.tz_convert('America/New_York').date())
                & (minute >= 570) & (minute < 960)
                & (bars.index < current.floor('min')))
        bars = bars.loc[mask].tail(self.lookback)
        if len(bars) < minimum_bars:
            raise ValueError('insufficient completed session bars')
        if (current-bars.index[-1]).total_seconds() > self.max_age_seconds:
            raise ValueError('stale market data')
        # Validate only data shape and quality here; scoring stays in the scout.
        required = {'open', 'high', 'low', 'close', 'volume'}
        if not required.issubset(bars.columns):
            raise ValueError('missing OHLCV fields')
        numeric = bars[list(required)].astype(float)
        import numpy as np
        if (not np.isfinite(numeric.to_numpy()).all()
                or (numeric[['open', 'high', 'low', 'close']] <= 0).any().any()
                or (numeric.volume < 0).any() or numeric.volume.sum() <= 0):
            raise ValueError('invalid price or volume')
        if ((numeric.high < numeric[['open', 'close', 'low']].max(axis=1)).any()
                or (numeric.low > numeric[['open', 'close', 'high']].min(axis=1)).any()):
            raise ValueError('inconsistent high/low prices')
        return bars

    def fetch(self, symbols, *, held_symbols=(), now=None):
        now = now or datetime.now(timezone.utc)
        symbols = list(dict.fromkeys(symbols))
        snapshot = MarketSnapshot()
        if not symbols:
            return snapshot
        started = time.monotonic()
        request = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Minute,
                                   start=now-timedelta(minutes=max(self.lookback*3, 90)),
                                   end=now, feed=DataFeed.IEX, limit=10000)
        snapshot.request_count = 1
        try:
            all_bars = self.client.get_stock_bars(request).df
        except Exception as exc:
            snapshot.transport_ok = False
            snapshot.rejected = {s: 'market data request failed: '+type(exc).__name__ for s in symbols}
        else:
            # A plain index cannot identify multiple symbols; do not reuse one
            # symbol's prices for the rest of the watchlist.
            if len(symbols) > 1 and not isinstance(all_bars.index, pd.MultiIndex):
                snapshot.rejected = {s: 'multi-symbol response missing symbol index' for s in symbols}
            else:
                for symbol in symbols:
                    try:
                        snapshot.bars[symbol] = self.clean(
                            all_bars, symbol, now,
                            minimum_bars=2 if symbol in held_symbols else max(10, self.lookback//2))
                    except (ValueError, KeyError, TypeError) as exc:
                        snapshot.rejected[symbol] = str(exc)
        snapshot.elapsed_seconds = round(time.monotonic()-started, 4)
        return snapshot
