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
- Anchors daily profit and loss guardrails to Alpaca's prior-day closing equity, so Railway restarts cannot reset them.
- Includes a walk-forward historical backtester for AMD, TSM, and TSLA.
- Persists every paper analysis cycle and paper trade in a SQLite journal.
- Provides 1-, 7-, and 30-day paper performance reports without placing orders.
- Runs an advisory Performance Agent each trading day to study win rate, profit
  factor, expectancy, drawdown, symbols, and signal-score ranges.
- Stores each performance snapshot and prints plain-English recommendations;
  it never changes trading rules or places orders.
- Uses an Exit Agent with an unchangeable hard stop, momentum-aware profit
  taking, an extended profit target, and trailing profit protection.
- Uses a Risk Agent to approve and size every new paper entry, cap total
  exposure, limit concurrent positions, and enforce daily guardrails.
- Runs a knowledge-only After-Hours Learning Agent once per closed-market UTC
  day. It compares bounded parameter variations on historical hourly bars using
  a chronological 70/30 train/test split, saves its recommendation, and cannot
  place orders or change the bot's settings. Daniel's approval is required
  before any suggested rule change is applied.

This is a test framework, not a guarantee of profit.

## September 19 repairs, built and tested on the mini PC

- Daily entry limits no longer disable exit management. A failure on one
  position does not prevent checking the others, including held symbols removed
  from the watchlist.
- Durable order intents are written before submission. Timeouts stay unresolved
  until the broker confirms the order; the bot does not blindly retry purchases.
- Only terminal broker fills enter performance accounting. Canceled partial
  fills use their executed quantity and average price. Pending partial fills stay
  pending, block new entries, and remain visible as broker positions.
- Pre-upgrade estimated records remain in SQLite but are excluded from verified
  reports. This preserves history without presenting estimates as actual fills.
- Entry attempts are capped at six per New York calendar day, with a 30-minute
  per-symbol cooldown, persisted across restarts. Configure `MAX_DAILY_ENTRIES`
  and `ENTRY_COOLDOWN_MINUTES`; these defaults are conservative controls, not
  optimized strategy parameters.
- Pending broker orders block new entries. Candidates execute in score rank
  order, one new order per scan.
- Signals require finite positive prices, usable volume, and completed regular
  session bars no older than `MAX_BAR_AGE_SECONDS` (default 180). Held positions
  may use two fresh bars; new entries still require the longer history.
- Backtests require the same dip gate as the scout, use next-bar opening entry
  prices, and assume 5 basis points of slippage per side. Research candidates
  are selected on training results alone. The hourly research is a simplified
  proxy: it does not reproduce the live portfolio limits or trailing exits and
  does not establish profitability for the minute-bar strategy.

If an order cannot be found after a submission timeout, entries remain paused
for review; inspect `order_intents` and the broker before resolving it. Broker
orders from before the upgrade are not imported automatically. Existing holdings
remain managed, and their exits use the broker's entry basis. Run only one
trading instance per paper account. This repair does not migrate the running
service from Railway to Windows.

The exit loop needs fresh market data and API availability; it cannot guarantee
execution during an outage.

## Expanded agent team

The runtime now coordinates nine specialized components: Scout, Execution,
Risk, Exit, Performance, After-Hours Research, Data Quality, Guardian, and
Research Validation. These are deterministic trading and analysis components;
they do not require an LLM API key or produce unsupervised strategy changes.

- **Data Quality Agent:** fetches the entire watchlist in one SDK request,
  validates symbol identity, OHLCV consistency, timestamps and freshness, and
  isolates bad or missing symbols. Ten symbols require one request per scan
  rather than ten (the SDK may paginate larger responses internally).
- **Guardian Agent:** persists per-subsystem failure counts and a five-minute
  entry pause after three consecutive failures. Exit checks still run first.
  Writes a status snapshot beside the journal as `guardian_status.json`.
- **Research Validation Agent:** requires enough trades, finite statistics,
  positive modeled holdout returns, and an improvement over current parameters.
  Qualifying symbols must agree on parameters before a review is suggested.
  Holdout simulations run only for the selected candidate and current baseline.
- **Exit Agent:** persists trailing peaks in SQLite, restores them after a
  restart, and resets them when a new position identity or entry basis appears.
- **Execution Agent:** validates before saving an intent, distinguishes an
  explicit broker rejection from ambiguous network errors, checks returned order
  identity and fill timestamps, and consumes partial sales across tracked lots.

A clock-only outage can use a recently verified open session for exits for at
most 90 seconds, bounded by the broker's next close. New entries remain blocked.
Without that evidence, orders and research pause until the clock is available.
One invalid research symbol no longer aborts studies of the others.

The expanded suite contains 75 tests, including broker-response faults,
persistent guardian and trailing state, batched data isolation, and team wiring.

Alpaca order lifecycle reference:
https://docs.alpaca.markets/us/docs/orders-at-alpaca

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
- `TRAILING_ARM_PCT` = `0.35`
- `TRAILING_GAP_PCT` = `0.20`
- `MAX_TOTAL_EXPOSURE` = `75`
- `MAX_OPEN_POSITIONS` = `3`
- `AFTER_HOURS_LOOKBACK_DAYS` = `180`
- `AFTER_HOURS_MIN_TEST_TRADES` = `3`
- `AFTER_HOURS_REPORT_PATH` = `/data/after_hours_recommendations.json`

## Railway persistent journal storage

Create a Railway Volume for the service and mount it at `/data`. Keep
`JOURNAL_DB_PATH=/data/trading_journal.db` so the SQLite paper journal survives
deploys and restarts. Without a mounted volume, the journal is ephemeral to
the container. The journal contains paper-trading records only; never store
Alpaca API keys in it or in the repository.

Daily guardrails use Alpaca's `last_equity` as the prior-day closing baseline,
so they remain anchored across Railway restarts instead of restarting from the
process's equity when the container comes back up.

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
