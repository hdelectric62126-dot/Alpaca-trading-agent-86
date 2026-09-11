import os
import time
import math
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

from strategy import score_signal
from scout import MarketScout
from journal import PerformanceAnalyzer, TradeJournal
from performance_agent import PerformanceAgent


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
    "SYMBOLS", "AMD,TSM,TSLA,NVDA,AAPL,MSFT,AMZN,META,GOOGL,AVGO"
).split(",") if s.strip()]
LOOKBACK_MINUTES = int(os.getenv("LOOKBACK_MINUTES", "30"))
ENTRY_DIP_PCT = float(os.getenv("ENTRY_DIP_PCT", "0.35")) / 100.0
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.45")) / 100.0
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.50")) / 100.0
MAX_TRADE_NOTIONAL = float(os.getenv("MAX_TRADE_NOTIONAL", "25"))
DAILY_PROFIT_TARGET = float(os.getenv("DAILY_PROFIT_TARGET", "10"))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "10"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
MIN_SIGNAL_SCORE = int(os.getenv("MIN_SIGNAL_SCORE", "60"))
SCOUT_TOP_N = int(os.getenv("SCOUT_TOP_N", "3"))
PERFORMANCE_DAYS = int(os.getenv("PERFORMANCE_DAYS", "7"))
PERFORMANCE_MIN_TRADES = int(os.getenv("PERFORMANCE_MIN_TRADES", "10"))

trading = TradingClient(API_KEY, SECRET_KEY, paper=True)
data = StockHistoricalDataClient(API_KEY, SECRET_KEY)
journal = TradeJournal()
scout = MarketScout(ENTRY_DIP_PCT, MIN_SIGNAL_SCORE, SCOUT_TOP_N)
performance_agent = PerformanceAgent(journal, PERFORMANCE_MIN_TRADES)


def market_is_open():
    try:
        return bool(trading.get_clock().is_open)
    except Exception as exc:
        print(f"[clock] {exc}")
        return False


def account_equity():
    acct = trading.get_account()
    return float(acct.equity)


def positions():
    return {p.symbol: p for p in trading.get_all_positions()}


def open_orders():
    req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
    return trading.get_orders(filter=req)


def has_open_order(symbol):
    return any(o.symbol == symbol for o in open_orders())


def recent_bars(symbol):
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=max(LOOKBACK_MINUTES * 3, 90))
    req = StockBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )
    bars = data.get_stock_bars(req).df
    if bars.empty:
        return None

    # alpaca-py returns a multi-index dataframe when symbols are requested.
    if isinstance(bars.index, pd.MultiIndex):
        try:
            bars = bars.xs(symbol)
        except Exception:
            return None

    bars = bars.tail(LOOKBACK_MINUTES)
    if len(bars) < max(10, LOOKBACK_MINUTES // 2):
        return None
    return bars


def submit_buy(symbol, price):
    notional = min(MAX_TRADE_NOTIONAL, max(1.0, MAX_TRADE_NOTIONAL))
    order = MarketOrderRequest(
        symbol=symbol,
        notional=round(notional, 2),
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
    result = trading.submit_order(order_data=order)
    print(f"[PAPER BUY] {symbol} approx ${notional:.2f} near {price:.2f} | order={result.id}")
    return result, notional


def submit_sell(symbol, qty, reason):
    qty = float(qty)
    if qty <= 0:
        return
    order = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    result = trading.submit_order(order_data=order)
    print(f"[PAPER SELL] {symbol} qty={qty} reason={reason} | order={result.id}")
    return result


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


def run():
    start_equity = account_equity()
    print("Paper agent started.")
    print(f"Symbols: {SYMBOLS}")
    print(f"Starting paper equity: ${start_equity:,.2f}")
    last_summary_date = None

    while True:
        try:
            if not market_is_open():
                print("[status] Market closed. Waiting...")
                time.sleep(max(POLL_SECONDS, 60))
                continue

            equity = account_equity()
            pnl = equity - start_equity

            if pnl >= DAILY_PROFIT_TARGET:
                print(f"[guard] Daily paper profit target reached: ${pnl:.2f}. No new entries.")
                time.sleep(POLL_SECONDS)
                continue

            if pnl <= -DAILY_LOSS_LIMIT:
                print(f"[guard] Daily paper loss limit reached: ${pnl:.2f}. No new entries.")
                time.sleep(POLL_SECONDS)
                continue

            current_date = datetime.now(timezone.utc).date()
            if current_date != last_summary_date:
                daily_summary()
                last_summary_date = current_date

            pos = positions()

            # Agent #2: collect and rank the entire watchlist before Agent #1
            # is allowed to consider a new entry.
            bars_by_symbol = {}
            for symbol in SYMBOLS:
                bars = recent_bars(symbol)
                if bars is None or bars.empty:
                    print(f"[data] Not enough bars for {symbol}")
                else:
                    bars_by_symbol[symbol] = bars

            ranked = scout.rank(bars_by_symbol)
            candidates = {item.symbol for item in scout.candidates(ranked)}
            leaderboard = ", ".join(
                f"{item.symbol}:{item.signal.score}{'*' if item.symbol in candidates else ''}"
                for item in ranked[:5]
            ) or "no market data"
            print(f"[SCOUT] ranked={leaderboard} | * passed to execution agent")

            for symbol in SYMBOLS:
                bars = bars_by_symbol.get(symbol)
                if bars is None:
                    continue

                last_price = float(bars["close"].iloc[-1])
                mean_price = float(bars["close"].mean())

                # Manage an existing position first.
                if symbol in pos:
                    p = pos[symbol]
                    entry = float(p.avg_entry_price)
                    qty = float(p.qty)

                    if last_price >= entry * (1 + TAKE_PROFIT_PCT):
                        if not has_open_order(symbol):
                            order = submit_sell(symbol, qty, "take profit")
                            journal.record_cycle(
                                symbol=symbol, current_price=last_price, market_data=bar_data(bars),
                                signal=score_signal(bars, ENTRY_DIP_PCT), decision="SELL", order=order,
                                quantity=qty, entry_price=entry, exit_price=last_price,
                                realized_pnl=(last_price - entry) * qty,
                            )
                            journal.close_trade(symbol=symbol, exit_price=last_price,
                                                realized_pnl=(last_price - entry) * qty,
                                                order_id=order.id)
                    elif last_price <= entry * (1 - STOP_LOSS_PCT):
                        if not has_open_order(symbol):
                            order = submit_sell(symbol, qty, "stop loss")
                            journal.record_cycle(
                                symbol=symbol, current_price=last_price, market_data=bar_data(bars),
                                signal=score_signal(bars, ENTRY_DIP_PCT), decision="SELL", order=order,
                                quantity=qty, entry_price=entry, exit_price=last_price,
                                realized_pnl=(last_price - entry) * qty,
                            )
                            journal.close_trade(symbol=symbol, exit_price=last_price,
                                                realized_pnl=(last_price - entry) * qty,
                                                order_id=order.id)
                    else:
                        signal = score_signal(bars, ENTRY_DIP_PCT)
                        print(
                            f"[HOLD] {symbol} last={last_price:.2f} entry={entry:.2f} "
                            f"mean={mean_price:.2f}"
                        )
                        journal.record_cycle(symbol=symbol, current_price=last_price,
                                             market_data=bar_data(bars), signal=signal, decision="HOLD",
                                             quantity=qty, entry_price=entry,
                                             unrealized_pnl=(last_price - entry) * qty)
                    continue

                # One simple mean-reversion entry:
                # buy when last price is ENTRY_DIP_PCT below the rolling mean.
                threshold = mean_price * (1 - ENTRY_DIP_PCT)
                signal = score_signal(bars, ENTRY_DIP_PCT)
                print(f"[SIGNAL] {symbol} score={signal.score}/100 | " + "; ".join(signal.reasons))
                if (
                    symbol in candidates
                    and not has_open_order(symbol)
                ):
                    order, notional = submit_buy(symbol, last_price)
                    quantity = notional / last_price
                    journal.record_cycle(symbol=symbol, current_price=last_price,
                                         market_data=bar_data(bars), signal=signal, decision="BUY",
                                         order=order, quantity=quantity, entry_price=last_price)
                    journal.record_trade(symbol=symbol, score=signal.score, quantity=quantity,
                                         entry_price=last_price, order_id=order.id, status="OPEN")
                else:
                    rejection_reason = []
                    if last_price > threshold:
                        rejection_reason.append("price is not below the dip threshold")
                    if signal.score < MIN_SIGNAL_SCORE:
                        rejection_reason.append(f"score {signal.score} is below minimum {MIN_SIGNAL_SCORE}")
                    if last_price <= threshold and signal.score >= MIN_SIGNAL_SCORE and symbol not in candidates:
                        rejection_reason.append(f"not in scout's top {SCOUT_TOP_N} opportunities")
                    if has_open_order(symbol):
                        rejection_reason.append("an open paper order already exists")
                    rejection_reason = "; ".join(rejection_reason)
                    print(
                        f"[WAIT] {symbol} last={last_price:.2f} "
                        f"mean={mean_price:.2f} trigger<={threshold:.2f} "
                        f"score>={MIN_SIGNAL_SCORE}"
                    )
                    journal.record_cycle(symbol=symbol, current_price=last_price,
                                         market_data=bar_data(bars), signal=signal, decision="REJECT",
                                         rejection_reason=rejection_reason)

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            print("Stopped.")
            break
        except Exception as exc:
            print(f"[error] {type(exc).__name__}: {exc}")
            time.sleep(max(POLL_SECONDS, 30))


if __name__ == "__main__":
    run()
