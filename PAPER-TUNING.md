# Stage-2 paper tuning

The current paper strategy is producing small dollar gains because the position sizes are deliberately small. A 60-point signal currently receives about half of the $25 maximum trade size, which is roughly $12.50.

The agent's after-hours research has already tested lower dip thresholds (including 0.25%) and wider exit combinations. The latest 10-symbol research run did **not** support a rule change across enough symbols, so this tuning does not loosen the entry signal.

## Optional sizing-only experiment

Set:

```
PAPER_TUNING_PROFILE=size2x
```

That changes only paper sizing:

- maximum trade notional: $25 -> $50
- maximum total exposure: $75 -> $100
- maximum open positions stays capped at 3
- dip threshold remains 0.35%
- minimum score remains 60
- take-profit remains 0.45%
- stop-loss remains 0.50%
- daily entry cap remains 6

The baseline remains the default. Nothing changes unless the profile is explicitly enabled.

This does not improve the strategy's statistical edge; it only tests how the same strategy behaves at a somewhat larger but still bounded paper exposure. Compare verified fills, expectancy, drawdown, and order behavior before considering any further change.
