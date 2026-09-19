import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock

from alpaca.common.exceptions import APIError
from execution import PaperExecution
from journal import TradeJournal


class ExecutionUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.journal = TradeJournal(':memory:')
        self.client = Mock()
        self.execution = PaperExecution(self.client, self.journal)
        self.client.submit_order.side_effect = self.accept

    def tearDown(self):
        self.journal.close()

    def accept(self, order_data):
        return NS(id=order_data.client_order_id, client_order_id=order_data.client_order_id,
                  symbol=order_data.symbol, side=order_data.side, status='new', filled_qty='0')

    def fill(self, order, quantity, price, status='filled'):
        order.status = status
        order.filled_qty = str(quantity)
        order.filled_avg_price = str(price)
        order.filled_at = datetime.now(timezone.utc)
        self.execution.reconcile_one(order.client_order_id, order)

    def test_invalid_requests_leave_no_intent_or_broker_call(self):
        cases = [dict(notional=-1), dict(notional=float('nan')), dict(notional=5, quantity=1),
                 dict(), dict(quantity=0), dict(notional=5, score=101), dict(notional=5, score=True)]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises((ValueError, TypeError)):
                self.execution.submit('AMD', 'buy', **kwargs)
        with self.assertRaises(ValueError):
            self.execution.submit('AMD', 'sell', quantity=1)
        self.assertEqual(len(self.execution.pending()), 0)
        self.client.submit_order.assert_not_called()

    def test_case_variants_cannot_bypass_pending_guard(self):
        self.execution.submit(' amd ', 'buy', notional=5)
        with self.assertRaises(RuntimeError):
            self.execution.submit('AMD', 'buy', notional=5)

    def test_mismatched_identity_does_not_account_fill(self):
        order = self.execution.submit('AMD', 'buy', notional=5)
        persisted_client_id = order.client_order_id
        order.status, order.filled_qty, order.filled_avg_price = 'filled', '.05', '100'
        order.filled_at = datetime.now(timezone.utc)
        for field, invalid in [('id', 'other'), ('symbol', 'TSLA'), ('side', 'sell'), ('client_order_id', 'wrong')]:
            original = getattr(order, field)
            setattr(order, field, invalid)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.execution.reconcile_one(persisted_client_id, order)
            setattr(order, field, original)
        self.assertEqual(self.journal.connection.execute('SELECT COUNT(*) FROM paper_trades').fetchone()[0], 0)
        self.assertEqual(len(self.execution.pending()), 1)

    def test_bad_timestamps_leave_fill_unresolved(self):
        order = self.execution.submit('AMD', 'buy', quantity=1)
        order.status, order.filled_qty, order.filled_avg_price = 'filled', '1', '100'
        for stamp in [None, 'bad', datetime.now(), datetime.now(timezone.utc) + timedelta(days=1),
                      datetime.now(timezone.utc) - timedelta(days=1)]:
            order.filled_at = stamp
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                self.execution.reconcile_one(order.client_order_id, order)
        self.assertEqual(len(self.execution.pending()), 1)

    def test_overfill_is_not_accounted(self):
        order = self.execution.submit('AMD', 'buy', quantity=1)
        with self.assertRaises(ValueError):
            self.fill(order, 2, 100)
        self.assertEqual(len(self.execution.pending()), 1)

    def test_partial_cancel_consumes_multiple_open_lots_once(self):
        for quantity in [.3, .7]:
            order = self.execution.submit('AMD', 'buy', quantity=quantity, score=80)
            self.fill(order, quantity, 100)
        sell = self.execution.submit('AMD', 'sell', quantity=1, entry_price=100)
        self.fill(sell, .5, 102, 'canceled')
        self.execution.reconcile_one(sell.client_order_id, sell)
        rows = self.journal.connection.execute("SELECT * FROM paper_trades WHERE status='OPEN'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]['quantity'], .5)
        closed = self.journal.connection.execute("SELECT * FROM paper_trades WHERE status='CLOSED'").fetchall()
        self.assertEqual(len(closed), 1)
        self.assertAlmostEqual(closed[0]['realized_pnl'], 1)

    def test_definitive_rejection_does_not_leave_pending(self):
        response = NS(status_code=422)
        self.client.submit_order.side_effect = APIError('{"message":"insufficient buying power"}', NS(response=response))
        with self.assertRaises(APIError):
            self.execution.submit('AMD', 'buy', notional=5)
        self.assertEqual(len(self.execution.pending()), 0)

    def test_ambiguous_errors_remain_pending_without_retry(self):
        for error in [TimeoutError(), APIError('{"message":"server error"}', NS(response=NS(status_code=500))),
                      APIError('{"message":"duplicate client_order_id"}', NS(response=NS(status_code=422)))]:
            with self.subTest(error=error):
                self.client.submit_order.side_effect = error
                symbol = 'S' + str(self.client.submit_order.call_count)
                with self.assertRaises(type(error)):
                    self.execution.submit(symbol, 'buy', notional=5)
                with self.assertRaises(RuntimeError):
                    self.execution.submit(symbol, 'buy', notional=5)
        self.assertEqual(self.client.submit_order.call_count, 3)
        self.assertEqual(len(self.execution.pending()), 3)
