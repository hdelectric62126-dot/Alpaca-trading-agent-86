"""Durable order intents and fill-confirmed accounting for paper orders."""

import math
import uuid
from datetime import datetime, timedelta, timezone

from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.common.exceptions import APIError


TERMINAL = {"filled", "canceled", "expired", "rejected"}


def value(obj, name, default=None):
    result = getattr(obj, name, default)
    return getattr(result, "value", result)


class PaperExecution:
    def __init__(self, trading, journal):
        self.trading, self.journal = trading, journal
        self.db = journal.connection
        self.db.execute('''CREATE TABLE IF NOT EXISTS order_intents (
            client_id TEXT PRIMARY KEY, order_id TEXT, symbol TEXT NOT NULL,
            side TEXT NOT NULL, score INTEGER NOT NULL, entry_price REAL,
            notional REAL, quantity REAL, created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', filled_qty REAL DEFAULT 0,
            filled_price REAL, completed_at TEXT)''')
        self.db.commit()

    def pending(self):
        return self.db.execute("SELECT * FROM order_intents WHERE status = 'pending'").fetchall()

    def submit(self, symbol, side, *, score=0, notional=None, quantity=None, entry_price=None):
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError('Symbol is required')
        symbol = symbol.strip().upper()
        side = OrderSide(side).value
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
            raise ValueError('Score must be an integer between 0 and 100')
        if (notional is None) == (quantity is None):
            raise ValueError('Provide exactly one of notional or quantity')
        for amount in (notional, quantity, entry_price):
            if amount is not None and (isinstance(amount, bool) or not math.isfinite(float(amount)) or float(amount) <= 0):
                raise ValueError('Order amounts must be finite and positive')
        if side == 'sell' and (quantity is None or entry_price is None):
            raise ValueError('Sell requires quantity and entry basis')
        if any(row['symbol'] == symbol for row in self.pending()):
            raise RuntimeError(f"Unresolved order for {symbol}; refusing duplicate")
        client_id = 'agent86-' + uuid.uuid4().hex
        # Local model validation must finish before the durable intent exists.
        request = MarketOrderRequest(symbol=symbol, side=OrderSide(side),
                                     time_in_force=TimeInForce.DAY,
                                     notional=notional, qty=quantity, client_order_id=client_id)
        # Commit before sending: an ambiguous timeout must never trigger another order.
        with self.db:
            self.db.execute('''INSERT INTO order_intents
                (client_id, symbol, side, score, entry_price, notional, quantity, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (client_id, symbol, side, score, entry_price, notional, quantity,
                 datetime.now(timezone.utc).isoformat()))
        try:
            order = self.trading.submit_order(order_data=request)
        except APIError as exc:
            # Only explicit request rejection is definitive. Duplicate IDs may
            # refer to accepted orders; timeouts/server errors remain pending.
            if exc.status_code in (400, 401, 403, 422) and not any(
                    word in str(exc).lower() for word in ('duplicate', 'client_order_id', 'timeout', 'timed out')):
                with self.db:
                    self.db.execute("UPDATE order_intents SET status='rejected', completed_at=? WHERE client_id=?",
                                    (datetime.now(timezone.utc).isoformat(), client_id))
            raise
        self._validate_identity(self.db.execute("SELECT * FROM order_intents WHERE client_id=?", (client_id,)).fetchone(), order)
        with self.db:
            self.db.execute("UPDATE order_intents SET order_id=? WHERE client_id=?",
                            (str(order.id), client_id))
        self.reconcile_one(client_id, order)
        return order

    def reconcile(self):
        healthy = True
        for row in self.pending():
            try:
                order = (self.trading.get_order_by_id(row['order_id']) if row['order_id']
                         else self.trading.get_order_by_client_id(row['client_id']))
                self.reconcile_one(row['client_id'], order)
            except Exception as exc:
                healthy = False
                print(f"[RECONCILE] {row['symbol']} unresolved: {type(exc).__name__}; entries paused")
        return healthy

    @staticmethod
    def _validate_identity(row, order):
        order_id = value(order, 'id')
        if order_id is None or not str(order_id).strip():
            raise ValueError('Broker order has no identity')
        for field, expected in (('client_order_id', row['client_id']), ('symbol', row['symbol']), ('side', row['side'])):
            actual = value(order, field)
            if actual is not None and str(actual) != expected:
                raise ValueError(f'Broker order {field} mismatch')
        if row['order_id'] and str(order_id) != row['order_id']:
            raise ValueError('Broker order ID mismatch')

    def reconcile_one(self, client_id, order):
        row = self.db.execute("SELECT * FROM order_intents WHERE client_id=?", (client_id,)).fetchone()
        if row is None or row['status'] != 'pending':
            return
        self._validate_identity(row, order)
        status = str(value(order, 'status'))
        if status not in TERMINAL:
            return
        qty = float(value(order, 'filled_qty', 0) or 0)
        price = float(value(order, 'filled_avg_price', 0) or 0)
        if not math.isfinite(qty) or qty < 0 or (qty > 0 and (not math.isfinite(price) or price <= 0)):
            raise ValueError('Invalid broker fill')
        if status == 'filled' and qty <= 0:
            raise ValueError('Filled order has no filled quantity')
        if row['quantity'] is not None and qty > float(row['quantity']) + 1e-8:
            raise ValueError('Broker filled quantity exceeds request')
        now = datetime.now(timezone.utc)
        completed = value(order, 'filled_at') or value(order, 'updated_at')
        if completed is None:
            if qty > 0:
                raise ValueError('Broker fill has no timestamp')
            completed = now
        if isinstance(completed, str):
            completed = datetime.fromisoformat(completed.replace('Z', '+00:00'))
        if not isinstance(completed, datetime) or completed.tzinfo is None or completed.utcoffset() is None:
            raise ValueError('Broker timestamp must include timezone')
        completed = completed.astimezone(timezone.utc)
        if completed > now + timedelta(minutes=1) or completed < datetime.fromisoformat(row['created_at']) - timedelta(seconds=5):
            raise ValueError('Broker timestamp is outside intent lifetime')
        completed = completed.isoformat()
        realized_pnl = None
        with self.db:
            if qty > 0:
                if row['side'] == 'buy':
                    self.db.execute('''INSERT INTO paper_trades
                        (symbol,score,order_id,status,quantity,entry_price,opened_at,fill_verified)
                        VALUES (?,?,?,'OPEN',?,?,?,1)''',
                        (row['symbol'], row['score'], str(order.id), qty, price, completed))
                else:
                    entry = float(row['entry_price'])
                    if not math.isfinite(entry) or entry <= 0:
                        raise ValueError('Invalid broker entry basis')
                    trades = self.db.execute("SELECT * FROM paper_trades WHERE symbol=? AND status='OPEN' AND fill_verified=1 ORDER BY id",
                                             (row['symbol'],)).fetchall()
                    trade = trades[0] if trades else None
                    # Each terminal sell records its actual executed quantity. Partial
                    # cancellation leaves the unsold position open for the next exit.
                    to_consume = qty
                    for lot in trades:
                        consumed = min(float(lot['quantity']), to_consume)
                        remaining = float(lot['quantity']) - consumed
                        self.db.execute("UPDATE paper_trades SET quantity=?, status=? WHERE id=?",
                                        (remaining, 'OPEN' if remaining > 1e-8 else 'CONSUMED', lot['id']))
                        to_consume -= consumed
                        if to_consume <= 1e-8:
                            break
                    realized_pnl = (price-entry)*qty
                    self.db.execute('''INSERT INTO paper_trades
                        (symbol,score,order_id,status,quantity,entry_price,exit_price,realized_pnl,opened_at,closed_at,fill_verified)
                        VALUES (?,?,?,'CLOSED',?,?,?,?,?,?,1)''',
                        (row['symbol'], trade['score'] if trade else 0, str(order.id), qty,
                         entry, price, realized_pnl,
                         trade['opened_at'] if trade else row['created_at'], completed))
            self.db.execute('''UPDATE order_intents SET status=?, order_id=?, filled_qty=?,
                            filled_price=?, completed_at=? WHERE client_id=?''',
                            (status, str(order.id), qty, price or None, completed, client_id))
        if qty > 0:
            if row['side'] == 'buy':
                print(f"[FILL VERIFIED] BUY {row['symbol']} qty={qty:.9f} price={price:.4f} broker_status={status}")
            else:
                print(f"[FILL VERIFIED] SELL {row['symbol']} qty={qty:.9f} price={price:.4f} realized_pnl=${realized_pnl:.4f} broker_status={status}")

    def entry_block(self, symbol, now=None, cooldown_minutes=30, daily_entries=6):
        now = now or datetime.now(timezone.utc)
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo('America/New_York'))
        start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
        count = self.db.execute("SELECT COUNT(*) FROM order_intents WHERE side='buy' AND created_at>=?", (start,)).fetchone()[0]
        if count >= daily_entries:
            return 'daily entry attempt cap reached'
        cutoff = (now-timedelta(minutes=cooldown_minutes)).isoformat()
        if self.db.execute("SELECT 1 FROM order_intents WHERE symbol=? AND (status='pending' OR created_at>=? OR completed_at>=?) LIMIT 1",
                           (symbol, cutoff, cutoff)).fetchone():
            return 'symbol cooldown or unresolved order'
        return None
