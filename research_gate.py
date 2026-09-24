"""Trusted external-research gate for paper entries.

Uses Alpaca's news REST endpoint and SEC EDGAR public JSON APIs. External
research may block or annotate a setup, but it never places orders.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import time

import requests


RISK_TERMS = (
    "bankruptcy", "chapter 11", "fraud", "accounting investigation",
    "sec investigation", "subpoena", "delisting", "trading halt",
    "recall", "data breach", "cyberattack", "restatement",
    "cuts guidance", "cut guidance", "lowers guidance", "lowered guidance",
    "misses estimates", "missed estimates",
)
MATERIAL_FORMS = {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A", "6-K", "20-F"}


@dataclass(frozen=True)
class ResearchDecision:
    allowed: bool
    reason: str
    news_headlines: tuple[str, ...]
    recent_filings: tuple[str, ...]
    sources_ok: bool


def headline_has_risk(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    return any(term in normalized for term in RISK_TERMS)


def recent_material_filings(forms, filing_dates, *, now=None, lookback_days=1):
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=lookback_days)).date()
    flagged = []
    for form, filing_date in zip(forms or [], filing_dates or []):
        if form not in MATERIAL_FORMS:
            continue
        try:
            date_value = datetime.strptime(str(filing_date), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if date_value >= cutoff:
            flagged.append(f"{form} filed {date_value.isoformat()}")
    return tuple(flagged)


class ResearchGate:
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        *,
        news_lookback_minutes: int = 120,
        cache_seconds: int = 300,
        sec_user_agent: str | None = None,
        timeout_seconds: float = 4.0,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.news_lookback_minutes = int(news_lookback_minutes)
        self.cache_seconds = int(cache_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.sec_user_agent = sec_user_agent or os.getenv(
            "SEC_USER_AGENT",
            "AlpacaTradingAgent/1.0 https://github.com/hdelectric62126-dot/Alpaca-trading-agent-86",
        )
        self.session = requests.Session()
        self._decision_cache = {}
        self._ticker_cache = None
        self._ticker_cache_at = 0.0

    def _headers_alpaca(self):
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
        }

    def _headers_sec(self):
        return {
            "User-Agent": self.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        }

    def _news(self, symbol: str, now: datetime):
        start = now - timedelta(minutes=self.news_lookback_minutes)
        response = self.session.get(
            "https://data.alpaca.markets/v1beta1/news",
            headers=self._headers_alpaca(),
            params={
                "symbols": symbol,
                "start": start.isoformat(),
                "end": now.isoformat(),
                "sort": "desc",
                "limit": 20,
                "include_content": "false",
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        articles = payload.get("news", []) if isinstance(payload, dict) else []
        headlines = tuple(str(row.get("headline", "")).strip() for row in articles if row.get("headline"))
        risky = []
        for row in articles:
            combined = f"{row.get('headline', '')} {row.get('summary', '')}"
            if headline_has_risk(combined):
                risky.append(str(row.get("headline", "")).strip() or "risk-language news item")
        return headlines, tuple(risky)

    def _ticker_map(self):
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

    def _filings(self, symbol: str, now: datetime):
        cik = self._ticker_map().get(symbol.upper())
        if cik is None:
            raise LookupError(f"SEC CIK not found for {symbol}")
        response = self.session.get(
            f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
            headers=self._headers_sec(),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        recent = payload.get("filings", {}).get("recent", {})
        return recent_material_filings(
            recent.get("form", []),
            recent.get("filingDate", []),
            now=now,
            lookback_days=1,
        )

    def review(self, symbol: str, *, now=None) -> ResearchDecision:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        key = symbol.upper()
        cached = self._decision_cache.get(key)
        now_mono = time.monotonic()
        if cached and now_mono - cached[0] < self.cache_seconds:
            return cached[1]

        issues = []
        news_headlines = ()
        recent_filings = ()
        sources_ok = True

        try:
            news_headlines, risky_news = self._news(key, now)
            if risky_news:
                issues.append("risk-language news: " + " | ".join(risky_news[:3]))
        except Exception as exc:
            sources_ok = False
            issues.append("news unavailable: " + type(exc).__name__)

        try:
            recent_filings = self._filings(key, now)
            if recent_filings:
                issues.append("recent material SEC filing: " + " | ".join(recent_filings[:3]))
        except Exception as exc:
            sources_ok = False
            issues.append("SEC unavailable: " + type(exc).__name__)

        decision = ResearchDecision(
            allowed=sources_ok and not issues,
            reason="trusted research passed" if sources_ok and not issues else "; ".join(issues),
            news_headlines=news_headlines,
            recent_filings=recent_filings,
            sources_ok=sources_ok,
        )
        self._decision_cache[key] = (now_mono, decision)
        return decision
