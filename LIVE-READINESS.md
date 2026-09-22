# Live-readiness preparation

This branch does **not** enable live trading. The hard paper-only runtime lock remains in place.

## What this preparation adds

- Broker-terminal fills are explicitly logged as `[FILL VERIFIED]`.
- Verified sell fills include realized paper P/L in the Railway log.
- `live_readiness.py` performs a read-only review of the persistent paper journal.
- The readiness tool refuses to mark the strategy eligible for human live review unless all configured evidence gates pass.

Default evidence gates:

- at least 50 broker-verified closed paper trades;
- at least 10 distinct New York trading days;
- positive cumulative realized paper P/L;
- positive expectancy per verified trade;
- profit factor of at least 1.10;
- zero unresolved order intents;
- no currently blocked Guardian subsystem.

These are conservative review gates, not a claim that a strategy is profitable or safe. Passing them does not switch accounts, change credentials, or submit a live order.

## Run on Railway or a local copy of the journal

```bash
python live_readiness.py --db /data/trading_journal.db
```

Exit code 0 means the evidence gates passed and the bot is eligible for a separate human live-trading review. Exit code 2 means keep paper testing.

## Current conversion rule

Do not add a live broker client or live credentials on this branch. A future live-capable branch should be created only after paper evidence is reviewed, with independent live-risk limits and an explicit manual enable gate.
