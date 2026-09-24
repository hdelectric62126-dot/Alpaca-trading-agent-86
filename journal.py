"""Persistent paper-trading journal and read-only performance analysis."""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone


DEFAULT_JOURNAL_PATH = "/data/trading_journal.db"
SCORE_RANGES = ((0, 19), (20, 39), (40, 59), (60, 79), (80, 100))


def journal_path():
    return os.getenv("JOURNAL_DB_PATH", DEFAULT_JOURNAL_PATH)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class TradeJournal:
    def __init__(self, path=None):
        self.path = path or journal_path()
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS analysis_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, symbol TEXT NOT NULL,
                current_price REAL NOT NULL, market_data TEXT NOT NULL,
                dip_pct REAL, rsi REAL, vwap REAL, volume REAL,
                volume_ratio REAL, momentum_pct REAL,
                component_scores TEXT NOT NULL,
                total_score INTEGER NOT NULL CHECK(total_score BETWEEN 0 AND 100),
                reasons TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN ('BUY', 'SELL', 'HOLD', 'REJECT')),
                rejection_reason TEXT, paper_order_id TEXT,
                paper_order_status TEXT, quantity REAL, entry_price REAL,
                exit_price REAL, realized_pnl REAL, unrealized_pnl REAL
            );
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL, score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
                order_id TEXT, status TEXT NOT NULL, quantity REAL NOT NULL,
                entry_price REAL, exit_price REAL, realized_pnl REAL,
                opened_at TEXT NOT NULL, closed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS performance_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, period_days INTEGER NOT NULL,
                report TEXT NOT NULL, recommendations TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cycles_timestamp ON analysis_cycles(timestamp);
            CREATE INDEX IF NOT EXISTS idx_trades_opened_at ON paper_trades(opened_at);
            """
        )
        self.connection.commit()

        columns = {row[1] for row in self.connection.execute('PRAGMA table_info(paper_trades)')}
        migrations = {
            'fill_verified': 'INTEGER NOT NULL DEFAULT 0',
            'entry_slippage_bps': 'REAL',
            'exit_slippage_bps': 'REAL',
            'entry_reason': 'TEXT',
            'exit_reason': 'TEXT',
        }
        changed = False
        for name, definition in migrations.items():
            if name not in columns:
                self.connection.execute(
                    f'ALTER TABLE paper_trades ADD COLUMN {name} {definition}'
                )
                changed = True
        if changed:
            self.connection.commit()

    def record_cycle(self, *, symbol, current_price, market_data, signal=None,
                     decision="HOLD", rejection_reason=None, order=None,
                     quantity=None, entry_price=None, exit_price=None,
                     realized_pnl=None, unrealized_pnl=None, timestamp=None):
        if signal is None:
            score = 0
            reasons = []
            component_scores = {}
            dip_pct = rsi = vwap = volume = volume_ratio = momentum_pct = None
        else:
            score = int(signal.score)
            reasons = list(signal.reasons)
            component_scores = signal.component_scores
            dip_pct = signal.dip_pct
            rsi = signal.rsi
            vwap = current_price / (1 + signal.price_vs_vwap_pct)
            volume = market_data.get("volume")
            volume_ratio = signal.volume_ratio
            momentum_pct = signal.momentum_pct
        order_id = _value(order, "id")
        order_status = _value(order, "status")
        self.connection.execute(
            """INSERT INTO analysis_cycles
            (timestamp, symbol, current_price, market_data, dip_pct, rsi, vwap,
             volume, volume_ratio, momentum_pct, component_scores, total_score,
             reasons, decision, rejection_reason, paper_order_id,
             paper_order_status, quantity, entry_price, exit_price, realized_pnl,
             unrealized_pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp or utc_now(), symbol, float(current_price), json.dumps(market_data),
             dip_pct, rsi, vwap, volume, volume_ratio, momentum_pct,
             json.dumps(component_scores), score, json.dumps(reasons), decision,
             rejection_reason, str(order_id) if order_id is not None else None,
             str(order_status) if order_status is not None else None, quantity,
             entry_price, exit_price, realized_pnl, unrealized_pnl),
        )
        self.connection.commit()

    def record_trade(self, *, symbol, score, quantity, entry_price=None,
                     exit_price=None, realized_pnl=None, order_id=None,
                     status="OPEN", opened_at=None, closed_at=None):
        self.connection.execute(
            """INSERT INTO paper_trades
            (symbol, score, order_id, status, quantity, entry_price, exit_price,
             realized_pnl, opened_at, closed_at, fill_verified)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (symbol, int(score), str(order_id) if order_id is not None else None,
             status, float(quantity), entry_price, exit_price, realized_pnl,
             opened_at or utc_now(), closed_at),
        )
        self.connection.commit()

    def close_trade(self, *, symbol, exit_price, realized_pnl, closed_at=None,
                    order_id=None):
        trade = self.connection.execute(
            "SELECT id FROM paper_trades WHERE symbol = ? AND status = 'OPEN' "
            "ORDER BY opened_at DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if trade is None:
            return
        self.connection.execute(
            "UPDATE paper_trades SET status = 'CLOSED', exit_price = ?, "
            "realized_pnl = ?, closed_at = ?, order_id = COALESCE(?, order_id) "
            "WHERE id = ?",
            (exit_price, realized_pnl, closed_at or utc_now(),
             str(order_id) if order_id is not None else None, trade["id"]),
        )
        self.connection.commit()

    def close(self):
        self.connection.close()

    def record_performance_snapshot(self, *, period_days, report,
                                    recommendations, timestamp=None):
        self.connection.execute(
            "INSERT INTO performance_snapshots "
            "(timestamp, period_days, report, recommendations) VALUES (?, ?, ?, ?)",
            (timestamp or utc_now(), int(period_days), json.dumps(report),
             json.dumps(recommendations)),
        )
        self.connection.commit()


def _value(value, name):
    if value is None:
        return None
    result = getattr(value, name, None)
    return result() if callable(result) else result


def _score_range(score):
    for lower, upper in SCORE_RANGES:
        if lower <= score <= upper:
            return f"{lower}-{upper}"
    return "unknown"


class PerformanceAnalyzer:
    def __init__(self, journal_or_path):
        self.journal = journal_or_path if isinstance(journal_or_path, TradeJournal) else TradeJournal(journal_or_path)

    def report(self, days):
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
        cycles = self.journal.connection.execute(
            "SELECT * FROM analysis_cycles WHERE timestamp >= ? ORDER BY timestamp",
            (cutoff.isoformat(),),
        ).fetchall()
        trades = self.journal.connection.execute(
            "SELECT * FROM paper_trades WHERE COALESCE(closed_at, opened_at) >= ? AND status != 'CONSUMED' ORDER BY COALESCE(closed_at, opened_at), id",
            (cutoff.isoformat(),),
        ).fetchall()
        legacy_count = sum(not trade['fill_verified'] for trade in trades)
        trades = [trade for trade in trades if trade['fill_verified']]
        closed = [trade for trade in trades if trade['status'] == 'CLOSED' and trade["realized_pnl"] is not None]
        profits = [float(trade["realized_pnl"]) for trade in closed]
        wins = [value for value in profits if value > 0]
        losses = [value for value in profits if value < 0]
        entry_slippage = [
            float(trade["entry_slippage_bps"]) for trade in closed
            if trade["entry_slippage_bps"] is not None
        ]
        exit_slippage = [
            float(trade["exit_slippage_bps"]) for trade in closed
            if trade["exit_slippage_bps"] is not None
        ]
        return {
            "days": int(days), "signals": len(cycles),
            "legacy_estimated_trades_excluded": legacy_count,
            "closed_trades": len(closed),
            "accepted_signals": sum(row["decision"] in ("BUY", "SELL") for row in cycles),
            "rejected_signals": sum(row["decision"] == "REJECT" for row in cycles),
            "paper_trades": len(trades), "wins": len(wins), "losses": len(losses),
            "win_rate_pct": _percent(len(wins), len(closed)),
            "total_profit_loss": round(sum(profits), 2),
            "average_profit_loss": _round_average(profits),
            "average_win": _round_average(wins), "average_loss": _round_average(losses),
            "average_entry_slippage_bps": _round_average(entry_slippage),
            "average_exit_slippage_bps": _round_average(exit_slippage),
            "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses else (float("inf") if wins else 0.0),
            "expectancy_per_trade": _round_average(profits),
            "maximum_drawdown": round(_maximum_drawdown(profits), 2),
            "by_symbol": self._group(closed, "symbol"),
            "by_score_range": self._group(closed, "score_range"),
        }

    def _group(self, trades, key):
        groups = {}
        for trade in trades:
            group = trade["symbol"] if key == "symbol" else _score_range(trade["score"])
            groups.setdefault(group, []).append(float(trade["realized_pnl"]))
        return {name: _trade_summary(values) for name, values in sorted(groups.items())}


def _trade_summary(values):
    wins = [value for value in values if value > 0]
    return {"trades": len(values), "wins": len(wins), "losses": len(values) - len(wins),
            "total_profit_loss": round(sum(values), 2), "win_rate_pct": _percent(len(wins), len(values))}


def _percent(numerator, denominator):
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def _round_average(values):
    return round(sum(values) / len(values), 2) if values else 0.0


def _maximum_drawdown(profits):
    equity = peak = drawdown = 0.0
    for profit in profits:
        equity += profit
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown
