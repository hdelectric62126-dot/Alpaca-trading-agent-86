"""Low-call multi-timeframe intraday context for selected paper-trade candidates.

One 1-minute Alpaca request is resampled locally into 5-minute and 15-minute
bars. This avoids three separate market-data calls while preserving consistent
OHLCV inputs for VWAP, EMA, ATR, relative-volume, and trend checks.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import time

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame


@dataclass(frozen=True)
class FrameMetrics:
    timeframe: str
    bars: int
    price: float
    ema8: float
    ema21: float
    ema50: float | None
    atr_pct: float
    trend: str


@dataclass(frozen=True)
class IntradayReport:
    available: bool
    allowed: bool
    reason: str
    vwap: float | None
    relative_volume: float | None
    one_minute: FrameMetrics | None
    five_minute: FrameMetrics | None
    fifteen_minute: FrameMetrics | None
    alignment: str


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _normalize(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise ValueError("no minute bars returned")
    bars = raw
    if isinstance(bars.index, pd.MultiIndex):
        try:
            bars = bars.xs(symbol, level=0)
        except (KeyError, ValueError):
            bars = bars.xs(symbol)
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(bars.columns):
        raise ValueError("minute OHLCV fields missing")
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError("minute timestamps must be timezone-aware")
    bars = bars.sort_index()
    bars = bars[~bars.index.duplicated(keep="last")]
    data = bars[list(required)].astype(float)
    if data.empty or not all(_finite(v) for v in data.to_numpy().ravel()):
        raise ValueError("invalid minute bars")
    if (data[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("non-positive minute price")
    if (data["volume"] < 0).any():
        raise ValueError("negative minute volume")
    return data


def _regular_session(bars: pd.DataFrame, now: datetime) -> pd.DataFrame:
    eastern = bars.index.tz_convert("America/New_York")
    minute = eastern.hour * 60 + eastern.minute
    mask = (minute >= 570) & (minute < 960)
    closed = bars.index < pd.Timestamp(now).floor("min")
    return bars.loc[mask & closed]


def _resample(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if minutes == 1:
        return bars.copy()
    eastern = bars.copy()
    eastern.index = eastern.index.tz_convert("America/New_York")
    grouped = eastern.resample(
        f"{minutes}min",
        origin="start_day",
        offset="30min",
        label="right",
        closed="left",
    ).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })
    grouped = grouped.dropna(subset=["open", "high", "low", "close"])
    return grouped


def _atr_pct(bars: pd.DataFrame, period: int = 14) -> float:
    previous = bars["close"].shift(1)
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - previous).abs(),
        (bars["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.tail(period).mean())
    price = float(bars["close"].iloc[-1])
    return atr / price if price > 0 else float("nan")


def _frame_metrics(bars: pd.DataFrame, name: str) -> FrameMetrics:
    if len(bars) < 21:
        raise ValueError(f"insufficient {name} bars")
    closes = bars["close"].astype(float)
    ema8 = float(closes.ewm(span=8, adjust=False).mean().iloc[-1])
    ema21 = float(closes.ewm(span=21, adjust=False).mean().iloc[-1])
    ema50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1]) if len(closes) >= 50 else None
    price = float(closes.iloc[-1])
    if price > ema8 > ema21:
        trend = "bullish"
    elif price < ema8 < ema21:
        trend = "bearish"
    else:
        trend = "mixed"
    return FrameMetrics(
        timeframe=name,
        bars=len(bars),
        price=price,
        ema8=ema8,
        ema21=ema21,
        ema50=ema50,
        atr_pct=_atr_pct(bars),
        trend=trend,
    )


def build_intraday_report(minute_bars: pd.DataFrame) -> IntradayReport:
    if minute_bars is None or len(minute_bars) < 60:
        return IntradayReport(
            False, False, "insufficient live data available: fewer than 60 one-minute bars",
            None, None, None, None, None, "unknown",
        )

    one = _resample(minute_bars, 1)
    five = _resample(minute_bars, 5)
    fifteen = _resample(minute_bars, 15)
    try:
        m1 = _frame_metrics(one, "1m")
        m5 = _frame_metrics(five, "5m")
        m15 = _frame_metrics(fifteen, "15m")
    except ValueError as exc:
        return IntradayReport(
            False, False, "insufficient live data available: " + str(exc),
            None, None, None, None, None, "unknown",
        )

    volumes = one["volume"].astype(float)
    typical = (one["high"] + one["low"] + one["close"]) / 3.0
    total_volume = float(volumes.sum())
    if total_volume <= 0:
        return IntradayReport(
            False, False, "insufficient live data available: unusable volume",
            None, None, m1, m5, m15, "unknown",
        )
    vwap = float((typical * volumes).sum() / total_volume)
    prior = float(volumes.iloc[-21:-1].mean()) if len(volumes) >= 21 else 0.0
    rvol = float(volumes.iloc[-1] / prior) if prior > 0 else None

    trends = (m1.trend, m5.trend, m15.trend)
    if trends == ("bullish", "bullish", "bullish"):
        alignment = "bullish"
    elif m5.trend == "bearish" and m15.trend == "bearish":
        alignment = "bearish"
    else:
        alignment = "mixed"

    price = m1.price
    failures = []
    if alignment == "bearish" and price < vwap:
        failures.append("5m and 15m trends are bearish while price is below VWAP")
    if m15.atr_pct > 0.04:
        failures.append(f"15m ATR {m15.atr_pct * 100:.2f}% is abnormally high")

    return IntradayReport(
        available=True,
        allowed=not failures,
        reason="multi-timeframe intraday context passed" if not failures else "; ".join(failures),
        vwap=vwap,
        relative_volume=rvol,
        one_minute=m1,
        five_minute=m5,
        fifteen_minute=m15,
        alignment=alignment,
    )


class IntradayContextAgent:
    def __init__(self, client, *, cache_seconds: int = 180, timeout_days: int = 5):
        self.client = client
        self.cache_seconds = int(cache_seconds)
        self.timeout_days = int(timeout_days)
        self._cache = {}

    def fetch(self, symbol: str, *, now=None) -> IntradayReport:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        symbol = symbol.strip().upper()
        cached = self._cache.get(symbol)
        mono = time.monotonic()
        if cached and mono - cached[0] < self.cache_seconds:
            return cached[1]

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=now - timedelta(days=self.timeout_days),
            end=now,
            feed=DataFeed.IEX,
            limit=5000,
        )
        try:
            raw = self.client.get_stock_bars(request).df
            bars = _regular_session(_normalize(raw, symbol), now)
            report = build_intraday_report(bars)
        except Exception as exc:
            report = IntradayReport(
                False, False,
                "insufficient live data available: intraday market data " + type(exc).__name__,
                None, None, None, None, None, "unknown",
            )
        self._cache[symbol] = (mono, report)
        return report
