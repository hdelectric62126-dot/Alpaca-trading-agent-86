import os
import time
import math
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

from strategy import score_signal
from execution import PaperExecution
from guardian_agent import GuardianAgent
from market_data_agent import MarketDataAgent
from dashboard_feed import start_dashboard_feed
from dashboard_runtime import publish_runtime
from scout import MarketScout
from journal import PerformanceAnalyzer, TradeJournal
from performance_agent import PerformanceAgent
from exit_agent import ExitAgent
from risk_agent import RiskAgent
from after_hours_agent import AfterHoursLearningAgent, ResearchCandidate
from paper_tuning import resolve_paper_sizing
from trade_gate import assess_market_regime, build_technical_plan
from research_gate import ResearchGate
from decision_intelligence import DecisionIntelligenceAgent


# ---------------------------
# PAPER-TRADING SAFETY GUARDS
# ---------------------------
PAPER_ONLY = os.getenv("PAPER_ONLY", "true").lower() == "true"
if not PAPER_ONLY:
    raise RuntimeError("This starter agent is locked to paper trading. Set PAPER_ONLY=true.")

API_KEY = os.environ["APCA_API_KEY_ID"]
SECRET_KEY = os.environ["APCA_API_SECRET_KEY"]

# Trading configuration
SYMBOLS = [s.strip().upper() for s in os.getenv(
    "SYMBOLS", "AMD,TSM,TSLA,NVDA,AAPL,MSFT,AMZN,META,GOOGL,AVGO,JPM,BAC,XOM,CVX,LLY,UNH,CAT,GE,WMT,COST"
).split(",") if s.strip()]
MARKET_BENCHMARKS = tuple(s.strip().upper() for s in os.getenv("MARKET_BENCHMARKS", "SPY,QQQ").split(",") if s.strip())
LOOKBACK_MINUTES = int(os.getenv("LOOKBACK_MINUTES", "30"))
ENTRY_DIP_PCT = float(os.getenv("ENTRY_DIP_PCT", "0.35")) / 100.0
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.45")) / 100.0
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.50")) / 100.0
PAPER_BANKROLL = float(os.getenv("PAPER_BANKROLL", "500"))
MAX_TRADE_NOTIONAL = float(os.getenv("MAX_TRADE_NOTIONAL", "25"))
DAILY_PROFIT_TARGET = float(os.getenv("DAILY_PROFIT_TARGET", "10"))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "10"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
MIN_SIGNAL_SCORE = int(os.getenv("MIN_SIGNAL_SCORE", "60"))
MIN_RELATIVE_VOLUME = float(os.getenv("MIN_RELATIVE_VOLUME", "1.0"))
MIN_REWARD_RISK = float(os.getenv("MIN_REWARD_RISK", "1.0"))
MAX_ATR_PCT = float(os.getenv("MAX_ATR_PCT", "2.0")) / 100.0
RESEARCH_GATE_ENABLED = os.getenv("RESEARCH_GATE_ENABLED", "true").lower() == "true"
NEWS_LOOKBACK_MINUTES = int(os.getenv("NEWS_LOOKBACK_MINUTES", "120"))
RESEARCH_CACHE_SECONDS = int(os.getenv("RESEARCH_CACHE_SECONDS", "300"))
INTELLIGENCE_GATE_ENABLED = os.getenv("INTELLIGENCE_GATE_ENABLED", "true").lower() == "true"
INTELLIGENCE_CACHE_SECONDS = int(os.getenv("INTELLIGENCE_CACHE_SECONDS", "900"))
SCREEN_MAX_PE = float(os.getenv("SCREEN_MAX_PE", "20"))
SCREEN_MIN_REVENUE_GROWTH_PCT = float(os.getenv("SCREEN_MIN_REVENUE_GROWTH_PCT", "8"))
FUNDAMENTAL_GATE_STRICT = os.getenv("FUNDAMENTAL_GATE_STRICT", "false").lower() == "true"
MAX_DAILY_CHASE_PCT = float(os.getenv("MAX_DAILY_CHASE_PCT", "2.5")) / 100.0
SCOUT_TOP_N = int(os.getenv("SCOUT_TOP_N", "3"))
PERFORMANCE_DAYS = int(os.getenv("PERFORMANCE_DAYS", "7"))
PERFORMANCE_MIN_TRADES = int(os.getenv("PERFORMANCE_MIN_TRADES", "10"))
TRAILING_ARM_PCT = float(os.getenv("TRAILING_ARM_PCT", "0.35")) / 100.0
TRAILING_GAP_PCT = float(os.getenv("TRAILING_GAP_PCT", "0.20")) / 100.0
MAX_TOTAL_EXPOSURE = float(os.getenv("MAX_TOTAL_EXPOSURE", "75"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
PAPER_TUNING_PROFILE = os.getenv("PAPER_TUNING_PROFILE", "baseline").strip().lower()
_sizing = resolve_paper_sizing(
    PAPER_TUNING_PROFILE,
    max_trade_notional=MAX_TRADE_NOTIONAL,
    max_total_exposure=MAX_TOTAL_EXPOSURE,
    max_open_positions=MAX_OPEN_POSITIONS,
)
MAX_TRADE_NOTIONAL = _sizing.max_trade_notional
MAX_TOTAL_EXPOSURE = _sizing.max_total_exposure
MAX_OPEN_POSITIONS = _sizing.max_open_positions
ENTRY_COOLDOWN_MINUTES = int(os.getenv("ENTRY_COOLDOWN_MINUTES", "30"))
MAX_DAILY_ENTRIES = int(os.getenv("MAX_DAILY_ENTRIES", "6"))
MAX_BAR_AGE_SECONDS = int(os.getenv("MAX_BAR_AGE_SECONDS", "180"))
AFTER_HOURS_LOOKBACK_DAYS = int(os.getenv("AFTER_HOURS_LOOKBACK_DAYS", "180"))
AFTER_HOURS_MIN_TEST_TRADES = int(os.getenv("AFTER_HOURS_MIN_TEST_TRADES", "3"))

trading = TradingClient(API_KEY, SECRET_KEY, paper=True)
data = StockHistoricalDataClient(API_KEY, SECRET_KEY)
journal = TradeJournal()
execution = PaperExecution(trading, journal)
guardian = GuardianAgent(journal.connection)
market_data_agent = MarketDataAgent(data, lookback=LOOKBACK_MINUTES, max_age_seconds=MAX_BAR_AGE_SECONDS)
scout = MarketScout(ENTRY_DIP_PCT, MIN_SIGNAL_SCORE, SCOUT_TOP_N)
performance_agent = PerformanceAgent(journal, PERFORMANCE_MIN_TRADES)
exit_agent = ExitAgent(TAKE_PROFIT_PCT, STOP_LOSS_PCT,
                       TRAILING_ARM_PCT, TRAILING_GAP_PCT, connection=journal.connection)
risk_agent = RiskAgent(
    minimum_score=MIN_SIGNAL_SCORE,
    max_trade_notional=MAX_TRADE_NOTIONAL,
    max_total_exposure=MAX_TOTAL_EXPOSURE,
    max_open_positions=MAX_OPEN_POSITIONS,
    daily_profit_target=DAILY_PROFIT_TARGET,
    daily_loss_limit=DAILY_LOSS_LIMIT,
    paper_bankroll=PAPER_BANKROLL,
)
after_hours_agent = AfterHoursLearningAgent(
    minimum_test_trades=AFTER_HOURS_MIN_TEST_TRADES,
)
research_gate = ResearchGate(
    API_KEY,
    SECRET_KEY,
    news_lookback_minutes=NEWS_LOOKBACK_MINUTES,
    cache_seconds=RESEARCH_CACHE_SECONDS,
)
decision_intelligence = DecisionIntelligenceAgent(
    data,
    cache_seconds=INTELLIGENCE_CACHE_SECONDS,
    max_pe=SCREEN_MAX_PE,
    min_revenue_growth_pct=SCREEN_MIN_REVENUE_GROWTH_PCT,
    strict_value_screen=FUNDAMENTAL_GATE_STRICT,
    max_chase_pct=MAX_DAILY_CHASE_PCT,
)
_clock_degraded = False
_verified_open_until = None


def market_is_open():
    global _clock_degraded, _verified_open_until
    try:
        clock = trading.get_clock()
        publish_runtime(journal.path, market=dict(is_open=bool(clock.is_open),
                        next_open=clock.next_open.isoformat(), next_close=clock.next_close.isoformat(),
                        observed_at=datetime.now(timezone.utc).isoformat()))
        _clock_degraded = False
        guardian.record_success('clock')
        now = datetime.now(timezone.utc)
        _verified_open_until = (min(clock.next_close, now + timedelta(seconds=90))
                                if clock.is_open else None)
        if not clock.is_open:
            print(f"[status] Market closed. next_open={clock.next_open}")
        return bool(clock.is_open)
    except Exception as exc:
        _clock_degraded = True
        guardian.record_failure('clock')
        now = datetime.now(timezone.utc)
        if _verified_open_until is not None and now < _verified_open_until:
            print('[CLOCK] using recently verified session for exits only')
            return True
        # Never submit queued overnight orders or start research on unknown state.
        print('[CLOCK] session unverified; orders and research paused')
        return None


def account_equity():
    acct = trading.get_account()
    publish_runtime(journal.path, account=dict(equity=float(acct.equity),
                    last_equity=float(acct.last_equity),
                    observed_at=datetime.now(timezone.utc).isoformat()))
    return float(acct.equity)


def account_daily_pnl():
    acct = trading.get_account()
    publish_runtime(journal.path, account=dict(equity=float(acct.equity),
                    last_equity=float(acct.last_equity),
                    observed_at=datetime.now(timezone.utc).isoformat()))
    return float(acct.equity) - float(acct.last_equity)


def positions():
    current = {p.symbol: p for p in trading.get_all_positions()}
    publish_runtime(journal.path, positions=[dict(symbol=p.symbol,quantity=float(p.qty),
                    entry_price=float(p.avg_entry_price),market_value=float(p.market_value),
                    unrealized_pnl=float(p.unrealized_pl)) for p in current.values()],
                    positions_observed_at=datetime.now(timezone.utc).isoformat())
    return current


def open_orders():
    req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
    return trading.get_orders(filter=req)


def has_open_order(symbol):
    return any(o.symbol == symbol for o in open_orders())


def recent_bars(symbol, minimum_bars=None):
    """Compatibility helper; the trading loop uses one watchlist-wide request."""
    result = market_data_agent.fetch([symbol], held_symbols=[symbol] if minimum_bars == 2 else [],
                                     now=datetime.now(timezone.utc))
    return result.bars.get(symbol)


def historical_hour_bars(symbol):
    """Fetch closed-market research data; this function never places orders."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=AFTER_HOURS_LOOKBACK_DAYS)
    req = StockBarsRequest(
        symbol_or_symbols=[symbol], timeframe=TimeFrame.Hour,
        start=start, end=end, feed=DataFeed.IEX,
    )
    bars = data.get_stock_bars(req).df
    if bars.empty:
        return None
    if isinstance(bars.index, pd.MultiIndex):
        try:
            bars = bars.xs(symbol)
        except Exception:
            return None
    return bars


def run_after_hours_learning():
    print("[AFTER HOURS] Market closed; starting knowledge-only historical research.")
    bars_by_symbol = {}
    for symbol in SYMBOLS:
        try:
            bars = historical_hour_bars(symbol)
            if bars is not None and not bars.empty:
                bars_by_symbol[symbol] = bars
        except Exception as exc:
            print(f'[RESEARCH DATA] {symbol}: {type(exc).__name__}; other symbols continue')
    current = ResearchCandidate(
        dip_threshold=ENTRY_DIP_PCT,
        minimum_score=MIN_SIGNAL_SCORE,
        take_profit=TAKE_PROFIT_PCT,
        stop_loss=STOP_LOSS_PCT,
    )
    report = after_hours_agent.run(bars_by_symbol, current)
    AfterHoursLearningAgent.log_summary(report)


def submit_buy(symbol, price, notional, score=0):
    notional = float(notional)
    if not math.isfinite(notional) or not 1 <= notional <= MAX_TRADE_NOTIONAL:
        raise ValueError('Buy notional is outside configured limits')
    order = execution.submit(symbol, 'buy', score=score, notional=round(notional, 2))
    print(f"[PAPER BUY SUBMITTED] {symbol} ${notional:.2f} order={order.id}")
    return order, notional


def submit_sell(symbol, qty, reason, entry_price):
    qty = float(qty)
    if not math.isfinite(qty) or qty <= 0:
        raise ValueError('Sell quantity must be finite and positive')
    order = execution.submit(symbol, 'sell', quantity=qty, entry_price=entry_price)
    print(f"[PAPER SELL SUBMITTED] {symbol} qty={qty} reason={reason} order={order.id}")
    return order


def bar_data(bars):
    row = bars.iloc[-1]
    return {name: float(value) for name, value in row.items() if name in {"open", "high", "low", "close", "volume", "trade_count", "vwap"}}


def daily_summary():
    report = PerformanceAnalyzer(journal).report(1)
    print(
        f"[PAPER DAILY] signals={report['signals']} accepted={report['accepted_signals']} "
        f"rejected={report['rejected_signals']} trades={report['paper_trades']} "
        f"wins={report['wins']} losses={report['losses']} "
        f"P/L=${report['total_profit_loss']:.2f} drawdown=${report['maximum_drawdown']:.2f}"
    )
    PerformanceAgent.log_summary(performance_agent.analyze(PERFORMANCE_DAYS))


def manage_positions(pos, bars_by_symbol, orders):
    pending_symbols = {row['symbol'] for row in execution.pending()}
    healthy = True
    for symbol, position in pos.items():
        try:
            bars = bars_by_symbol.get(symbol)
            if bars is None:
                healthy = False
                print(f"[EXIT DATA] {symbol}: no fresh completed bars; exit decision unavailable")
                continue
            if symbol in pending_symbols or any(o.symbol == symbol for o in orders):
                continue
            entry, qty = float(position.avg_entry_price), float(position.qty)
            if not math.isfinite(entry) or entry <= 0 or not math.isfinite(qty) or qty <= 0:
                healthy = False
                print(f"[EXIT] {symbol}: invalid or unsupported position requires review")
                continue
            tracked = journal.connection.execute(
                "SELECT order_id FROM paper_trades WHERE symbol=? AND status='OPEN' AND fill_verified=1 ORDER BY id DESC LIMIT 1",
                (symbol,)).fetchone()
            identity = tracked['order_id'] if tracked else 'broker-existing'
            decision = exit_agent.decide(symbol, entry, bars, entry_identity=identity)
            if decision.action == 'SELL':
                order = submit_sell(symbol, qty, decision.reason, entry)
                journal.record_cycle(symbol=symbol, current_price=float(bars['close'].iloc[-1]),
                                     market_data=bar_data(bars), decision='SELL', order=order)
            else:
                price = float(bars['close'].iloc[-1])
                journal.record_cycle(symbol=symbol, current_price=price, market_data=bar_data(bars),
                                     signal=score_signal(bars, ENTRY_DIP_PCT), decision='HOLD',
                                     quantity=qty, entry_price=entry, unrealized_pnl=(price-entry)*qty)
        except Exception as exc:
            healthy = False
            print(f"[EXIT] {symbol}: {type(exc).__name__}: {exc}")
    if healthy:
        guardian.record_success('exit_management')
    else:
        guardian.record_failure('exit_management')
    return healthy


def run_cycle():
    # Reconcile even while closed, so late fills and cancellations are recorded.
    reconciled = execution.reconcile()
    if reconciled:
        guardian.record_success('reconciliation')
    else:
        guardian.record_failure('reconciliation')
    market_state = market_is_open()
    if market_state is not True:
        return market_state
    pos = positions()
    orders = open_orders()
    snapshot = market_data_agent.fetch([*pos, *SYMBOLS, *MARKET_BENCHMARKS], held_symbols=pos)
    bars_by_symbol = snapshot.bars
    if snapshot.transport_ok:
        guardian.record_success('market_data')
    else:
        guardian.record_failure('market_data')
    print(f'[DATA AGENT] ready={len(snapshot.bars)} rejected={len(snapshot.rejected)} '
          f'requests={snapshot.request_count} elapsed={snapshot.elapsed_seconds:.3f}s')
    for symbol, reason in snapshot.rejected.items():
        print(f'[DATA QUALITY] {symbol}: {reason}')
    # Daily limits and entry failures must never bypass position protection.
    exits_healthy = manage_positions(pos, bars_by_symbol, orders)
    if _clock_degraded or not guardian.entry_allowed() or not exits_healthy:
        print('[GUARDIAN] entries paused; exit management remains active')
        return True
    pnl = account_daily_pnl()
    if not reconciled or execution.pending():
        print('[ENTRY GUARD] unresolved orders; entries paused')
        return True
    if pnl >= DAILY_PROFIT_TARGET or pnl <= -DAILY_LOSS_LIMIT:
        print(f'[ENTRY GUARD] daily limit reached: ${pnl:.2f}; exits remain active')
        return True
    if not math.isfinite(pnl):
        print('[ENTRY GUARD] invalid daily P/L')
        return True
    # Re-read after exits; include all broker pending orders in exposure gating.
    pos, orders = positions(), open_orders()
    if orders:
        print('[ENTRY GUARD] broker has pending orders; entries paused')
        return True
    for symbol in list(exit_agent.high_water):
        if symbol not in pos:
            exit_agent.clear(symbol)
    regime = assess_market_regime(bars_by_symbol, MARKET_BENCHMARKS)
    print(f"[MARKET REGIME] {'PASS' if regime.allowed else 'BLOCK'}: {regime.reason}; weak={list(regime.weak_benchmarks)}")
    ranked = scout.rank({s: b for s, b in bars_by_symbol.items() if s in SYMBOLS and s not in pos})
    candidates = scout.candidates(ranked)
    selected = {item.symbol for item in candidates}
    for item in ranked:
        if item.symbol not in selected:
            if item.price > item.trigger_price:
                reason = 'dip threshold not met'
            elif not item.reversal_confirmed:
                reason = 'dip still falling; reversal not confirmed'
            elif item.signal.score < MIN_SIGNAL_SCORE:
                reason = 'signal score below minimum'
            else:
                reason = 'eligible setup outside scout top selection'
            journal.record_cycle(symbol=item.symbol, current_price=item.price,
                                 market_data=bar_data(bars_by_symbol[item.symbol]), signal=item.signal,
                                 decision='REJECT', rejection_reason=reason)
    print('[SCOUT] ' + ', '.join(
        f"{item.symbol}:{item.signal.score}:{'READY' if item.entry_ready else 'WAIT'}"
        for item in ranked[:5]))
    # Execute in rank order, rather than watchlist order.
    for item in candidates:
        if not regime.allowed:
            journal.record_cycle(symbol=item.symbol, current_price=item.price,
                                 market_data=bar_data(bars_by_symbol[item.symbol]), signal=item.signal,
                                 decision='REJECT', rejection_reason='market regime: ' + regime.reason)
            continue
        try:
            plan = build_technical_plan(
                bars_by_symbol[item.symbol],
                take_profit_pct=TAKE_PROFIT_PCT,
                stop_loss_pct=STOP_LOSS_PCT,
                min_volume_ratio=MIN_RELATIVE_VOLUME,
                min_reward_risk=MIN_REWARD_RISK,
                max_atr_pct=MAX_ATR_PCT,
            )
        except (ValueError, TypeError) as exc:
            journal.record_cycle(symbol=item.symbol, current_price=item.price,
                                 market_data=bar_data(bars_by_symbol[item.symbol]), signal=item.signal,
                                 decision='REJECT', rejection_reason='technical gate unavailable: ' + str(exc))
            continue
        print(f"[QUALITY GATE] {item.symbol} {'PASS' if plan.allowed else 'BLOCK'} "
              f"rr={plan.reward_risk:.2f} vol={plan.volume_ratio:.2f}x "
              f"atr={plan.atr_pct*100:.2f}% vwap={'YES' if plan.vwap_reclaimed else 'NO'}")
        if not plan.allowed:
            journal.record_cycle(symbol=item.symbol, current_price=item.price,
                                 market_data=bar_data(bars_by_symbol[item.symbol]), signal=item.signal,
                                 decision='REJECT', rejection_reason='technical gate: ' + plan.reason)
            continue
        if INTELLIGENCE_GATE_ENABLED:
            intelligence = decision_intelligence.review(item.symbol, current_price=item.price)
            tech = intelligence.technical
            fundamentals = intelligence.fundamentals
            rr_text = f"{tech.reward_risk:.2f}" if tech.reward_risk is not None else "n/a"
            pe_text = f"{fundamentals.pe_ratio:.1f}" if fundamentals.pe_ratio is not None else "n/a"
            confidence_text = (
                f"{fundamentals.match_confidence_pct:.0f}%"
                if fundamentals.match_confidence_pct is not None else "n/a"
            )
            print(
                f"[DECISION INTELLIGENCE] {item.symbol} "
                f"{'PASS' if intelligence.allowed else 'BLOCK'} "
                f"trend={tech.trend} sma50={tech.sma50 if tech.sma50 is not None else 'n/a'} "
                f"sma200={tech.sma200 if tech.sma200 is not None else 'n/a'} "
                f"rsi={tech.rsi if tech.rsi is not None else 'n/a'} "
                f"macd={tech.macd_state} rr={rr_text} pe={pe_text} "
                f"screen={confidence_text} reason={intelligence.reason}"
            )
            if not intelligence.allowed:
                journal.record_cycle(
                    symbol=item.symbol,
                    current_price=item.price,
                    market_data=bar_data(bars_by_symbol[item.symbol]),
                    signal=item.signal,
                    decision='REJECT',
                    rejection_reason='decision intelligence: ' + intelligence.reason,
                )
                continue
        if RESEARCH_GATE_ENABLED:
            try:
                research = research_gate.review(item.symbol)
            except Exception as exc:
                journal.record_cycle(symbol=item.symbol, current_price=item.price,
                                     market_data=bar_data(bars_by_symbol[item.symbol]), signal=item.signal,
                                     decision='REJECT', rejection_reason='research gate unavailable: ' + type(exc).__name__)
                print(f"[RESEARCH GATE] {item.symbol} BLOCK: unavailable {type(exc).__name__}")
                continue
            print(f"[RESEARCH GATE] {item.symbol} {'PASS' if research.allowed else 'BLOCK'} "
                  f"news={len(research.news_headlines)} filings={len(research.recent_filings)} "
                  f"reason={research.reason}")
            if not research.allowed:
                journal.record_cycle(symbol=item.symbol, current_price=item.price,
                                     market_data=bar_data(bars_by_symbol[item.symbol]), signal=item.signal,
                                     decision='REJECT', rejection_reason='research gate: ' + research.reason)
                continue
        block = execution.entry_block(item.symbol, cooldown_minutes=ENTRY_COOLDOWN_MINUTES,
                                      daily_entries=MAX_DAILY_ENTRIES)
        risk = risk_agent.assess(score=item.signal.score, positions=pos, daily_pnl=pnl)
        if block or not risk.approved:
            journal.record_cycle(symbol=item.symbol, current_price=item.price,
                                 market_data=bar_data(bars_by_symbol[item.symbol]), signal=item.signal,
                                 decision='REJECT', rejection_reason=block or risk.reason)
            continue
        order, notional = submit_buy(item.symbol, item.price, risk.notional, item.signal.score)
        journal.record_cycle(symbol=item.symbol, current_price=item.price,
                             market_data=bar_data(bars_by_symbol[item.symbol]), signal=item.signal,
                             decision='BUY', order=order)
        # Refresh on next scan rather than assuming a submitted order has filled.
        break
    return True


def run():
    try:
        start_dashboard_feed(journal.path)
    except OSError:
        print('[DASHBOARD] feed unavailable; trading loop continues')
    print(f'Paper agent started. Symbols: {SYMBOLS}')
    broker_equity = account_equity()
    print(f'Broker paper equity: ${broker_equity:,.2f} (not used as strategy bankroll)')
    print(f'Virtual strategy bankroll: ${PAPER_BANKROLL:,.2f}')
    print(f'Entry controls: daily cap={MAX_DAILY_ENTRIES}, cooldown={ENTRY_COOLDOWN_MINUTES}m')
    print(f'Paper sizing profile: {PAPER_TUNING_PROFILE}; trade_cap=${MAX_TRADE_NOTIONAL:.2f}; exposure_cap=${MAX_TOTAL_EXPOSURE:.2f}; max_positions={MAX_OPEN_POSITIONS}')
    print(f'Trusted research gate: {"enabled" if RESEARCH_GATE_ENABLED else "disabled"}; news_lookback={NEWS_LOOKBACK_MINUTES}m; cache={RESEARCH_CACHE_SECONDS}s')
    print(
        f'Decision intelligence: {"enabled" if INTELLIGENCE_GATE_ENABLED else "disabled"}; '
        f'daily_cache={INTELLIGENCE_CACHE_SECONDS}s; max_pe={SCREEN_MAX_PE:g}; '
        f'min_revenue_growth={SCREEN_MIN_REVENUE_GROWTH_PCT:g}%; '
        f'fundamental_strict={FUNDAMENTAL_GATE_STRICT}; max_chase={MAX_DAILY_CHASE_PCT*100:.2f}%'
    )
    print('[AGENT TEAM] Scout, Market Regime, Technical Quality Gate, Daily Technical Intelligence, SEC Fundamental Scanner, Quant Screen, Trusted Research, Execution, Risk, Exit, Performance, After-Hours Research, Data Quality, Guardian, Research Validation')
    last_summary_date = last_after_hours_date = None
    while True:
        try:
            opened = run_cycle()
            publish_runtime(journal.path, phase='market_open' if opened is True else ('market_closed' if opened is False else 'clock_unavailable'))
            if opened is False:
                account_equity()
                positions()
            guardian.record_success('runtime')
            guardian.write_status(Path(journal.path).with_name('guardian_status.json'))
            current_date = datetime.now(timezone.utc).date()
            if opened and current_date != last_summary_date:
                daily_summary()
                last_summary_date = current_date
            if opened is False and current_date != last_after_hours_date:
                publish_runtime(journal.path, phase='research')
                run_after_hours_learning()
                publish_runtime(journal.path, phase='market_closed')
                last_after_hours_date = current_date
            time.sleep(max(POLL_SECONDS, 1))
        except KeyboardInterrupt:
            print('Stopped.')
            break
        except Exception as exc:
            guardian.record_failure('runtime')
            print(f'[error] {type(exc).__name__}: {exc}')
            time.sleep(max(POLL_SECONDS, 30))


if __name__ == '__main__':
    run()
