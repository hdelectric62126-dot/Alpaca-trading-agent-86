# Alpaca Trading Agent 86 — Paper Trading Starter

This starter project is intentionally locked to **Alpaca paper trading**.

## What it does

- Watches AMD, TSM, and TSLA by default.
- Calculates a short rolling average from 1-minute bars.
- Looks for a small dip below that average.
- Places a small paper market buy when the configured dip threshold is met.
- Uses a configurable take-profit and stop-loss.
- Stops opening new positions after the configured daily paper profit target or daily paper loss limit is reached.

This is a test framework, not a guarantee of profit.

## Railway environment variables

Add these in Railway under the deployed service's **Variables** tab.

Required:

- `APCA_API_KEY_ID` = your Alpaca paper API key ID
- `APCA_API_SECRET_KEY` = your Alpaca paper secret key
- `PAPER_ONLY` = `true`

Recommended starter settings:

- `SYMBOLS` = `AMD,TSM,TSLA`
- `LOOKBACK_MINUTES` = `30`
- `ENTRY_DIP_PCT` = `0.35`
- `TAKE_PROFIT_PCT` = `0.45`
- `STOP_LOSS_PCT` = `0.50`
- `MAX_TRADE_NOTIONAL` = `25`
- `DAILY_PROFIT_TARGET` = `10`
- `DAILY_LOSS_LIMIT` = `10`
- `POLL_SECONDS` = `60`

## Important

Do not put API keys in GitHub files.

The U.S.-listed ADR ticker for Taiwan Semiconductor Manufacturing Company is `TSM`, not `TSMC`.

Before using real money, paper-test the strategy over many market sessions and review fills, slippage, losses, and behavior around volatile moves.
