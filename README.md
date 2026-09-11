# Alpaca Trading Agent 86 — Paper Trading Starter

This starter project is intentionally locked to **Alpaca paper trading**.

## What it does

- Runs a market-scanner agent across AMD, TSM, TSLA, NVDA, AAPL, MSFT,
  AMZN, META, GOOGL, and AVGO by default.
- Ranks every setup and feeds only the strongest three eligible opportunities
  to the execution agent.
- Calculates a short rolling average from 1-minute bars.
- Scores each possible entry from 0 to 100 using the dip, RSI, VWAP, volume, and improving momentum.
- Writes plain-English scoring reasons to the Railway logs.
- Requires the configurable signal score before any paper buy.
- Places a small paper market buy when the configured dip threshold is met.
- Uses a configurable take-profit and stop-loss.
- Stops opening new positions after the configured daily paper profit target or daily paper loss limit is reached.
- Includes a walk-forward historical backtester for AMD, TSM, and TSLA.
- Persists every paper analysis cycle and paper trade in a SQLite journal.
- Provides 1-, 7-, and 30-day paper performance reports without placing orders.
- Runs an advisory Performance Agent each trading day to study win rate, profit
  factor, expectancy, drawdown, symbols, and signal-score ranges.
- Stores each performance snapshot and prints plain-English recommendations;
  it never changes trading rules or places orders.

This is a test framework, not a guarantee of profit.

## Railway environment variables

Add these in Railway under the deployed service's **Variables** tab.

Required:

- `APCA_API_KEY_ID` = your Alpaca paper API key ID
- `APCA_API_SECRET_KEY` = your Alpaca paper secret key
- `PAPER_ONLY` = `true`

Recommended starter settings:

- `SYMBOLS` = `AMD,TSM,TSLA,NVDA,AAPL,MSFT,AMZN,META,GOOGL,AVGO`
- `LOOKBACK_MINUTES` = `30`
- `ENTRY_DIP_PCT` = `0.35`
- `TAKE_PROFIT_PCT` = `0.45`
- `STOP_LOSS_PCT` = `0.50`
- `MAX_TRADE_NOTIONAL` = `25`
- `DAILY_PROFIT_TARGET` = `10`
- `DAILY_LOSS_LIMIT` = `10`
- `POLL_SECONDS` = `60`
- `MIN_SIGNAL_SCORE` = `60`
- `SCOUT_TOP_N` = `3`
- `JOURNAL_DB_PATH` = `/data/trading_journal.db`
- `PERFORMANCE_DAYS` = `7`
- `PERFORMANCE_MIN_TRADES` = `10`

## Railway persistent journal storage

Create a Railway Volume for the service and mount it at `/data`. Keep
`JOURNAL_DB_PATH=/data/trading_journal.db` so the SQLite paper journal survives
deploys and restarts. Without a mounted volume, the journal is ephemeral to
the container. The journal contains paper-trading records only; never store
Alpaca API keys in it or in the repository.

## Important

Do not put API keys in GitHub files.

The U.S.-listed ADR ticker for Taiwan Semiconductor Manufacturing Company is `TSM`, not `TSMC`.

Before using real money, paper-test the strategy over many market sessions and review fills, slippage, losses, and behavior around volatile moves.

## Run the backtester

With the same Alpaca API variables available in your shell, run:

```bash
python backtest.py --symbols AMD,TSM,TSLA --days 365 --minimum-score 60
```

The backtester processes each historical bar in order and reports ending cash, return, trade count, and win rate. It is an analysis tool only and never submits orders.

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Generate a paper report

These commands only read SQLite and never contact Alpaca or place orders:

```bash
python report.py --days 1
python report.py --days 7
python report.py --days 30
```
