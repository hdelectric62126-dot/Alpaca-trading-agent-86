"""Read-only live-readiness review for the paper trading journal.

This tool never imports Alpaca's trading client and cannot place orders.
It does not enable live trading. It only decides whether the accumulated
paper evidence is mature enough for a human live-trading review.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def _table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _drawdown(values):
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _local_day(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.astimezone(NY).date().isoformat()


def evaluate(
    path,
    *,
    min_closed_trades=50,
    min_trading_days=10,
    min_profit_factor=1.10,
    min_expectancy=0.0,
):
    root = Path(path).expanduser().resolve()
    db = sqlite3.connect(root.as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        if not _table_exists(db, "paper_trades"):
            raise RuntimeError("paper_trades table is missing")
        rows = db.execute(
            """SELECT symbol, realized_pnl, closed_at
               FROM paper_trades
               WHERE fill_verified=1 AND status='CLOSED'
                 AND realized_pnl IS NOT NULL AND closed_at IS NOT NULL
               ORDER BY closed_at, id"""
        ).fetchall()
        profits = [float(row["realized_pnl"]) for row in rows]
        wins = [value for value in profits if value > 0]
        losses = [value for value in profits if value < 0]
        trading_days = sorted({_local_day(row["closed_at"]) for row in rows})

        pending = 0
        if _table_exists(db, "order_intents"):
            pending = db.execute(
                "SELECT COUNT(*) FROM order_intents WHERE status='pending'"
            ).fetchone()[0]

        blocked = []
        if _table_exists(db, "guardian_health"):
            now = time.time()
            blocked = [
                dict(row)
                for row in db.execute(
                    """SELECT category, failures, blocked_until, updated_at
                       FROM guardian_health
                       WHERE blocked_until > ?
                       ORDER BY category""",
                    (now,),
                ).fetchall()
            ]

        gross_wins = sum(wins)
        gross_losses = abs(sum(losses))
        profit_factor = (
            gross_wins / gross_losses
            if gross_losses > 0
            else (math.inf if gross_wins > 0 else 0.0)
        )
        expectancy = sum(profits) / len(profits) if profits else 0.0

        checks = {
            "verified_closed_trades": len(rows) >= int(min_closed_trades),
            "distinct_trading_days": len(trading_days) >= int(min_trading_days),
            "positive_total_realized_pnl": sum(profits) > 0,
            "positive_expectancy": expectancy > float(min_expectancy),
            "profit_factor": profit_factor >= float(min_profit_factor),
            "no_pending_orders": pending == 0,
            "guardian_not_blocked": not blocked,
        }
        return {
            "mode": "PAPER_READ_ONLY",
            "live_trading_enabled": False,
            "ready_for_live_review": all(checks.values()),
            "thresholds": {
                "min_closed_trades": int(min_closed_trades),
                "min_trading_days": int(min_trading_days),
                "min_profit_factor": float(min_profit_factor),
                "min_expectancy": float(min_expectancy),
            },
            "metrics": {
                "verified_closed_trades": len(rows),
                "trading_days": len(trading_days),
                "first_trading_day": trading_days[0] if trading_days else None,
                "last_trading_day": trading_days[-1] if trading_days else None,
                "wins": len(wins),
                "losses": len(losses),
                "win_rate_pct": round(100 * len(wins) / len(rows), 2) if rows else 0.0,
                "realized_pnl": round(sum(profits), 6),
                "expectancy_per_trade": round(expectancy, 6),
                "profit_factor": None if math.isinf(profit_factor) else round(profit_factor, 4),
                "profit_factor_infinite": math.isinf(profit_factor),
                "maximum_drawdown": round(_drawdown(profits), 6),
                "pending_orders": int(pending),
                "blocked_guardian_subsystems": [row["category"] for row in blocked],
            },
            "checks": checks,
            "note": (
                "Passing these checks only makes the strategy eligible for a human live-trading review. "
                "It does not establish profitability or enable live orders."
            ),
        }
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        default=os.getenv("JOURNAL_DB_PATH", "/data/trading_journal.db"),
        help="Path to the persistent paper journal.",
    )
    parser.add_argument("--min-closed-trades", type=int, default=50)
    parser.add_argument("--min-trading-days", type=int, default=10)
    parser.add_argument("--min-profit-factor", type=float, default=1.10)
    parser.add_argument("--min-expectancy", type=float, default=0.0)
    args = parser.parse_args()
    result = evaluate(
        args.db,
        min_closed_trades=args.min_closed_trades,
        min_trading_days=args.min_trading_days,
        min_profit_factor=args.min_profit_factor,
        min_expectancy=args.min_expectancy,
    )
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["ready_for_live_review"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
