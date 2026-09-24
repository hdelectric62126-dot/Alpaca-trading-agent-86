"""Deterministic daily technical + SEC fundamental intelligence for paper entries.

The agent never invents missing values. Reports are computed from retrieved data
or explicitly marked "insufficient live data available".
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import os
import time

import pandas as pd
import requests
from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame


INSUFFICIENT = "insufficient live data available"


@dataclass(frozen=True)
class TechnicalReport:
    available: bool
    allowed: bool
    reason: str
    price: float | None
    sma20: float | None
    sma50: float | None
    sma200: float | None
    rsi: float | None
    rsi_divergence: str
    macd: float | None
    macd_signal: float | None
    macd_state: str
    support: float | None
    resistance: float | None
    ideal_entry: float | None
    stop_loss: float | None
    reward_risk: float | None
    trend: str


@dataclass(frozen=True)
class FundamentalReport:
    available: bool
    allowed: bool
    reason: str
    pe_ratio: float | None
    free_cash_flow: float | None
    fcf_yield_pct: float | None
    revenue_growth_pct: float | None
    gross_margin_pct: float | None
    gross_margin_change_pp: float | None
    debt_to_equity: float | None
    inventory_growth_pct: float | None
    match_confidence_pct: float | None
    matched_criteria: tuple[str, ...]
    red_flags: tuple[str, ...]


@dataclass(frozen=True)
class IntelligenceDecision:
    allowed: bool
    reason: str
    technical: TechnicalReport
    fundamentals: FundamentalReport


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _rsi_series(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    flat = (gain == 0) & (loss == 0)
    only_gains = (gain > 0) & (loss == 0)
    return rsi.mask(flat, 50.0).mask(only_gains, 100.0).fillna(50.0)


def build_daily_technical_report(
    bars: pd.DataFrame,
    *,
    current_price: float | None = None,
    max_chase_pct: float = 0.025,
) -> TechnicalReport:
    missing = (
        False, False, f"{INSUFFICIENT}: fewer than 200 daily bars",
        None, None, None, None, None, "unknown", None, None, "unknown",
        None, None, None, None, None, "unknown",
    )
    if bars is None or len(bars) < 200:
        return TechnicalReport(*missing)

    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(bars.columns):
        values = list(missing)
        values[2] = f"{INSUFFICIENT}: daily OHLCV fields missing"
        return TechnicalReport(*values)

    data = bars[["open", "high", "low", "close", "volume"]].astype(float).dropna().copy()
    if len(data) < 200 or not all(_finite(v) for v in data.to_numpy().ravel()):
        values = list(missing)
        values[2] = f"{INSUFFICIENT}: daily bars invalid"
        return TechnicalReport(*values)

    closes = data["close"]
    highs = data["high"]
    lows = data["low"]
    price = float(current_price) if _finite(current_price) and float(current_price) > 0 else float(closes.iloc[-1])

    sma20 = float(closes.tail(20).mean())
    sma50 = float(closes.tail(50).mean())
    sma200 = float(closes.tail(200).mean())

    rsi_series = _rsi_series(closes)
    rsi = float(rsi_series.iloc[-1])

    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd_series = ema12 - ema26
    macd_signal_series = macd_series.ewm(span=9, adjust=False).mean()
    macd = float(macd_series.iloc[-1])
    macd_signal = float(macd_signal_series.iloc[-1])
    if macd > macd_signal and float(macd_series.iloc[-2]) <= float(macd_signal_series.iloc[-2]):
        macd_state = "bullish crossover"
    elif macd < macd_signal and float(macd_series.iloc[-2]) >= float(macd_signal_series.iloc[-2]):
        macd_state = "bearish crossover"
    elif macd >= macd_signal:
        macd_state = "above signal"
    else:
        macd_state = "below signal"

    recent = data.tail(14)
    recent_rsi = rsi_series.loc[recent.index]
    split = max(5, len(recent) // 2)
    first = recent.iloc[:split]
    second = recent.iloc[split:]
    first_rsi = recent_rsi.iloc[:split]
    second_rsi = recent_rsi.iloc[split:]
    if (
        not second.empty
        and float(second["low"].min()) < float(first["low"].min())
        and float(second_rsi.min()) > float(first_rsi.min())
    ):
        divergence = "bullish"
    elif (
        not second.empty
        and float(second["high"].max()) > float(first["high"].max())
        and float(second_rsi.max()) < float(first_rsi.max())
    ):
        divergence = "bearish"
    else:
        divergence = "none"

    support = float(lows.iloc[-61:-1].min())
    resistance = float(highs.iloc[-61:-1].max())
    ideal_entry = min(max(support * 1.003, min(sma20, sma50)), price)
    stop_loss = min(price * 0.99, support * 0.995)
    risk = max(price - stop_loss, price * 0.001)
    reward_risk = max(0.0, resistance - price) / risk

    if price >= sma50 >= sma200:
        trend = "uptrend"
    elif price >= sma200:
        trend = "mixed"
    else:
        trend = "downtrend"

    failures = []
    if price < sma50 and sma50 < sma200:
        failures.append("price and 50-day average are below the 200-day trend")
    if macd_state == "bearish crossover" and price < sma20:
        failures.append("bearish MACD crossover below the 20-day average")
    if divergence == "bearish" and rsi >= 65 and price >= resistance * 0.98:
        failures.append("bearish RSI divergence near resistance")
    chase_pct = (price - ideal_entry) / price if price > 0 else 0.0
    if chase_pct > max_chase_pct:
        failures.append(f"price is {chase_pct * 100:.2f}% above the preferred pullback entry")

    return TechnicalReport(
        available=True,
        allowed=not failures,
        reason="daily technical report passed" if not failures else "; ".join(failures),
        price=price,
        sma20=sma20,
        sma50=sma50,
        sma200=sma200,
        rsi=rsi,
        rsi_divergence=divergence,
        macd=macd,
        macd_signal=macd_signal,
        macd_state=macd_state,
        support=support,
        resistance=resistance,
        ideal_entry=ideal_entry,
        stop_loss=stop_loss,
        reward_risk=reward_risk,
        trend=trend,
    )


def _concept(payload: dict, taxonomy: str, names: tuple[str, ...]) -> dict | None:
    facts = payload.get("facts", {}).get(taxonomy, {})
    for name in names:
        if name in facts:
            return facts[name]
    return None


def _entries(fact: dict | None, units: tuple[str, ...]) -> list[dict]:
    if not fact:
        return []
    unit_map = fact.get("units", {})
    for unit in units:
        rows = unit_map.get(unit)
        if rows:
            return list(rows)
    for rows in unit_map.values():
        if rows:
            return list(rows)
    return []


def _annual_values(payload: dict, names: tuple[str, ...], units=("USD",)) -> list[tuple[str, float]]:
    fact = _concept(payload, "us-gaap", names)
    rows = _entries(fact, units)
    by_end = {}
    for row in rows:
        if row.get("form") not in {"10-K", "10-K/A", "20-F"}:
            continue
        val = row.get("val")
        end = row.get("end")
        if not end or not _finite(val):
            continue
        start = row.get("start")
        if start:
            try:
                duration = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
            except ValueError:
                duration = 365
            if duration < 250:
                continue
        filed = str(row.get("filed", ""))
        existing = by_end.get(end)
        if existing is None or filed >= existing[0]:
            by_end[end] = (filed, float(val))
    return [(end, by_end[end][1]) for end in sorted(by_end)]


def _latest_point(
    payload: dict,
    taxonomy: str,
    names: tuple[str, ...],
    units=("USD",),
) -> tuple[str, float] | None:
    rows = _entries(_concept(payload, taxonomy, names), units)
    candidates = []
    for row in rows:
        if row.get("form") not in {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "6-K"}:
            continue
        val = row.get("val")
        end = row.get("end")
        if end and _finite(val):
            candidates.append((end, str(row.get("filed", "")), float(val)))
    if not candidates:
        return None
    end, _, val = max(candidates, key=lambda x: (x[0], x[1]))
    return end, val


def _latest_two_annual(payload: dict, names: tuple[str, ...], units=("USD",)):
    values = _annual_values(payload, names, units)
    return values[-2:] if len(values) >= 2 else values


def _sum_latest_points(payload: dict, concept_groups: tuple[tuple[str, ...], ...]) -> float | None:
    values = []
    for names in concept_groups:
        point = _latest_point(payload, "us-gaap", names, ("USD",))
        if point is not None:
            values.append(point[1])
    return sum(values) if values else None


def build_fundamental_report(
    payload: dict,
    *,
    current_price: float,
    max_pe: float = 20.0,
    min_revenue_growth_pct: float = 8.0,
    strict_value_screen: bool = False,
) -> FundamentalReport:
    if not isinstance(payload, dict) or not _finite(current_price) or current_price <= 0:
        return FundamentalReport(
            False, not strict_value_screen, INSUFFICIENT,
            None, None, None, None, None, None, None, None, None, (), (),
        )

    revenue = _latest_two_annual(payload, (
        "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
    ))
    gross_profit = _latest_two_annual(payload, ("GrossProfit",))
    cfo = _annual_values(payload, ("NetCashProvidedByUsedInOperatingActivities",))
    capex = _annual_values(payload, (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForAdditionsToPropertyPlantAndEquipment",
    ))
    eps = _annual_values(payload, ("EarningsPerShareDiluted",), ("USD/shares", "USD / shares"))
    inventory = _latest_two_annual(payload, ("InventoryNet",))

    equity_point = _latest_point(payload, "us-gaap", (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ), ("USD",))
    debt = _sum_latest_points(payload, (
        ("LongTermDebtCurrent", "LongTermDebtAndFinanceLeaseObligationsCurrent"),
        ("LongTermDebtNoncurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"),
        ("ShortTermBorrowings",),
    ))
    shares_point = _latest_point(payload, "dei", ("EntityCommonStockSharesOutstanding",), ("shares",))

    revenue_growth = None
    if len(revenue) >= 2 and revenue[-2][1] != 0:
        revenue_growth = (revenue[-1][1] / revenue[-2][1] - 1) * 100

    gross_margin = None
    gross_margin_change = None
    if revenue and gross_profit:
        rev_map = dict(revenue)
        gp_map = dict(gross_profit)
        common = sorted(set(rev_map) & set(gp_map))
        if common:
            latest = common[-1]
            if rev_map[latest] != 0:
                gross_margin = gp_map[latest] / rev_map[latest] * 100
        if len(common) >= 2:
            prior, latest = common[-2], common[-1]
            if rev_map[prior] != 0 and rev_map[latest] != 0:
                gross_margin_change = (
                    gp_map[latest] / rev_map[latest] - gp_map[prior] / rev_map[prior]
                ) * 100

    free_cash_flow = None
    if cfo:
        latest_cfo_end, latest_cfo = cfo[-1]
        capex_map = dict(capex)
        if latest_cfo_end in capex_map:
            free_cash_flow = latest_cfo - capex_map[latest_cfo_end]

    pe_ratio = current_price / eps[-1][1] if eps and eps[-1][1] > 0 else None

    fcf_yield = None
    if free_cash_flow is not None and shares_point and shares_point[1] > 0:
        market_cap = current_price * shares_point[1]
        if market_cap > 0:
            fcf_yield = free_cash_flow / market_cap * 100

    debt_to_equity = None
    if debt is not None and equity_point and equity_point[1] > 0:
        debt_to_equity = debt / equity_point[1]

    inventory_growth = None
    if len(inventory) >= 2 and inventory[-2][1] != 0:
        inventory_growth = (inventory[-1][1] / inventory[-2][1] - 1) * 100

    red_flags = []
    if gross_margin_change is not None and gross_margin_change <= -5.0:
        red_flags.append(f"gross margin compressed {abs(gross_margin_change):.1f} percentage points")
    if debt_to_equity is not None and debt_to_equity >= 3.0:
        red_flags.append(f"debt-to-equity is elevated at {debt_to_equity:.2f}")
    if revenue_growth is not None and revenue_growth <= -10.0:
        red_flags.append(f"annual revenue contracted {abs(revenue_growth):.1f}%")
    if free_cash_flow is not None and free_cash_flow < 0:
        red_flags.append("annual free cash flow is negative")
    if (
        inventory_growth is not None
        and revenue_growth is not None
        and inventory_growth > 20.0
        and inventory_growth > revenue_growth + 20.0
    ):
        red_flags.append(
            f"inventory growth {inventory_growth:.1f}% materially exceeds revenue growth {revenue_growth:.1f}%"
        )

    criteria = []
    matched = []
    if pe_ratio is not None:
        criteria.append("P/E")
        if pe_ratio < max_pe:
            matched.append(f"P/E {pe_ratio:.1f} < {max_pe:.1f}")
    if free_cash_flow is not None:
        criteria.append("FCF")
        if free_cash_flow > 0:
            matched.append("free cash flow positive")
    if revenue_growth is not None:
        criteria.append("RevenueGrowth")
        if revenue_growth > min_revenue_growth_pct:
            matched.append(
                f"revenue growth {revenue_growth:.1f}% > {min_revenue_growth_pct:.1f}%"
            )

    confidence = (len(matched) / len(criteria) * 100) if criteria else None
    available = len(criteria) >= 2
    severe = any(
        flag.startswith("gross margin compressed")
        or flag.startswith("debt-to-equity")
        or flag.startswith("annual revenue contracted")
        or flag.startswith("inventory growth")
        for flag in red_flags
    )
    value_pass = available and len(matched) == len(criteria)
    allowed = not severe and (value_pass if strict_value_screen else True)

    if not available:
        reason = INSUFFICIENT
    elif severe:
        reason = "; ".join(red_flags)
    elif strict_value_screen and not value_pass:
        reason = "quantitative value/growth screen did not meet all available thresholds"
    else:
        reason = "fundamental quality screen passed" if not red_flags else "; ".join(red_flags)

    return FundamentalReport(
        available=available,
        allowed=allowed,
        reason=reason,
        pe_ratio=pe_ratio,
        free_cash_flow=free_cash_flow,
        fcf_yield_pct=fcf_yield,
        revenue_growth_pct=revenue_growth,
        gross_margin_pct=gross_margin,
        gross_margin_change_pp=gross_margin_change,
        debt_to_equity=debt_to_equity,
        inventory_growth_pct=inventory_growth,
        match_confidence_pct=confidence,
        matched_criteria=tuple(matched),
        red_flags=tuple(red_flags),
    )


class DecisionIntelligenceAgent:
    def __init__(
        self,
        market_client,
        *,
        cache_seconds: int = 900,
        sec_user_agent: str | None = None,
        timeout_seconds: float = 4.0,
        max_pe: float = 20.0,
        min_revenue_growth_pct: float = 8.0,
        strict_value_screen: bool = False,
        max_chase_pct: float = 0.025,
    ):
        self.market_client = market_client
        self.cache_seconds = int(cache_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.max_pe = float(max_pe)
        self.min_revenue_growth_pct = float(min_revenue_growth_pct)
        self.strict_value_screen = bool(strict_value_screen)
        self.max_chase_pct = float(max_chase_pct)
        self.sec_user_agent = sec_user_agent or os.getenv(
            "SEC_USER_AGENT",
            "AlpacaTradingAgent/1.0 https://github.com/hdelectric62126-dot/Alpaca-trading-agent-86",
        )
        self.session = requests.Session()
        self._cache = {}
        self._ticker_cache = None
        self._ticker_cache_at = 0.0

    def _daily_bars(self, symbol: str, now: datetime) -> pd.DataFrame:
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=now - timedelta(days=430),
            end=now,
            feed=DataFeed.IEX,
            limit=500,
        )
        bars = self.market_client.get_stock_bars(request).df
        if isinstance(bars.index, pd.MultiIndex):
            bars = bars.xs(symbol, level=0)
        return bars.sort_index()

    def _ticker_map(self) -> dict[str, int]:
        now_mono = time.monotonic()
        if self._ticker_cache is not None and now_mono - self._ticker_cache_at < 21600:
            return self._ticker_cache
        response = self.session.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": self.sec_user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        mapping = {}
        for row in payload.values():
            ticker = str(row.get("ticker", "")).upper()
            cik = row.get("cik_str")
            if ticker and cik is not None:
                mapping[ticker] = int(cik)
        self._ticker_cache = mapping
        self._ticker_cache_at = now_mono
        return mapping

    def _company_facts(self, symbol: str) -> dict:
        cik = self._ticker_map().get(symbol.upper())
        if cik is None:
            raise LookupError(f"SEC CIK not found for {symbol}")
        response = self.session.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
            headers={"User-Agent": self.sec_user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def review(self, symbol: str, *, current_price: float, now=None) -> IntelligenceDecision:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        key = symbol.upper()
        cached = self._cache.get(key)
        now_mono = time.monotonic()
        if cached and now_mono - cached[0] < self.cache_seconds:
            return cached[1]

        try:
            technical = build_daily_technical_report(
                self._daily_bars(key, now),
                current_price=current_price,
                max_chase_pct=self.max_chase_pct,
            )
        except Exception as exc:
            technical = TechnicalReport(
                False, False, f"{INSUFFICIENT}: daily market data {type(exc).__name__}",
                None, None, None, None, None, "unknown", None, None, "unknown",
                None, None, None, None, None, "unknown",
            )

        try:
            fundamentals = build_fundamental_report(
                self._company_facts(key),
                current_price=current_price,
                max_pe=self.max_pe,
                min_revenue_growth_pct=self.min_revenue_growth_pct,
                strict_value_screen=self.strict_value_screen,
            )
        except Exception as exc:
            fundamentals = FundamentalReport(
                False, not self.strict_value_screen,
                f"{INSUFFICIENT}: SEC company facts {type(exc).__name__}",
                None, None, None, None, None, None, None, None, None, (), (),
            )

        failures = []
        if not technical.allowed:
            failures.append("technical: " + technical.reason)
        if not fundamentals.allowed:
            failures.append("fundamental: " + fundamentals.reason)

        decision = IntelligenceDecision(
            allowed=not failures,
            reason="decision intelligence passed" if not failures else "; ".join(failures),
            technical=technical,
            fundamentals=fundamentals,
        )
        self._cache[key] = (now_mono, decision)
        return decision
