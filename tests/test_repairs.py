import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

os.environ.setdefault('APCA_API_KEY_ID', 'test-key')
os.environ.setdefault('APCA_API_SECRET_KEY', 'test-secret')
os.environ.setdefault('JOURNAL_DB_PATH', tempfile.mktemp(suffix='.db'))
import bot
from execution import PaperExecution
from journal import TradeJournal, PerformanceAnalyzer
from strategy import score_signal, walk_forward_backtest
from after_hours_agent import AfterHoursLearningAgent, ResearchCandidate
from test_strategy import make_bars
from market_data_agent import MarketSnapshot


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.j = TradeJournal(':memory:')
        self.client = Mock()
        self.e = PaperExecution(self.client, self.j)

    def tearDown(self):
        self.j.close()

    def order(self, status='new', qty='0', price=None):
        return NS(id='order-1', status=status, filled_qty=qty, filled_avg_price=price,
                  filled_at=datetime.now(timezone.utc))

    def test_submission_does_not_claim_fill(self):
        self.client.submit_order.return_value = self.order()
        self.e.submit('AMD', 'buy', score=80, notional=25)
        self.assertEqual(self.j.connection.execute('SELECT count(*) FROM paper_trades').fetchone()[0], 0)
        self.assertEqual(len(self.e.pending()), 1)

    def test_fill_reconciliation_is_idempotent_and_uses_actual_price(self):
        self.client.submit_order.return_value = self.order()
        self.e.submit('AMD', 'buy', score=80, notional=25)
        key = self.e.pending()[0]['client_id']
        self.e.reconcile_one(key, self.order('filled', '.2', '124.9'))
        self.e.reconcile_one(key, self.order('filled', '.2', '124.9'))
        rows = self.j.connection.execute('SELECT * FROM paper_trades').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['entry_price'], 124.9)
        self.assertEqual(rows[0]['quantity'], .2)

    def test_partial_cancel_sell_records_only_executed_quantity(self):
        self.client.submit_order.return_value = self.order('filled', '1', '100')
        self.e.submit('AMD', 'buy', score=80, notional=100)
        self.client.submit_order.return_value = self.order('canceled', '.4', '102')
        self.e.submit('AMD', 'sell', quantity=1, entry_price=100)
        report = PerformanceAnalyzer(self.j).report(1)
        self.assertAlmostEqual(report['total_profit_loss'], .8)
        remaining = self.j.connection.execute("SELECT quantity FROM paper_trades WHERE status='OPEN'").fetchone()[0]
        self.assertAlmostEqual(remaining, .6)

    def test_timeout_persists_intent_and_blocks_duplicate(self):
        self.client.submit_order.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            self.e.submit('AMD', 'buy', notional=25)
        e2 = PaperExecution(self.client, self.j)
        self.assertEqual(len(e2.pending()), 1)
        with self.assertRaises(RuntimeError):
            e2.submit('AMD', 'buy', notional=25)
        self.client.get_order_by_client_id.return_value = self.order('filled', '.25', '100')
        self.assertTrue(e2.reconcile())
        self.assertEqual(len(e2.pending()), 0)

    def test_rejected_order_never_becomes_trade(self):
        self.client.submit_order.return_value = self.order('rejected')
        self.e.submit('AMD', 'buy', notional=25)
        self.assertEqual(len(self.e.pending()), 0)
        self.assertEqual(PerformanceAnalyzer(self.j).report(1)['paper_trades'], 0)

    def test_cooldown_and_daily_cap_survive_new_execution_instance(self):
        self.client.submit_order.return_value = self.order('filled', '.25', '100')
        self.e.submit('AMD', 'buy', notional=25)
        e2 = PaperExecution(self.client, self.j)
        self.assertIn('cooldown', e2.entry_block('AMD'))
        self.assertIn('cap', e2.entry_block('TSM', daily_entries=1))
        self.assertIsNone(e2.entry_block('AMD', now=datetime.now(timezone.utc)+timedelta(days=1)))

    def test_realized_loss_locks_symbol_until_next_trading_day(self):
        now = datetime.now(timezone.utc)
        self.j.connection.execute(
            """INSERT INTO paper_trades
            (symbol,score,status,quantity,entry_price,exit_price,realized_pnl,opened_at,closed_at,fill_verified)
            VALUES ('AMD',80,'CLOSED',1,100,99,-1,?,?,1)""",
            ((now-timedelta(minutes=30)).isoformat(), now.isoformat()),
        )
        self.j.connection.commit()
        self.assertIn('locked after a realized loss', self.e.entry_block('AMD', now=now))
        self.assertIsNone(self.e.entry_block('AMD', now=now+timedelta(days=1)))

    def test_legacy_estimates_excluded_from_performance(self):
        self.j.connection.execute("INSERT INTO paper_trades (symbol,score,status,quantity,realized_pnl,opened_at) VALUES ('AMD',60,'CLOSED',1,99,?)", (datetime.now(timezone.utc).isoformat(),))
        self.j.connection.commit()
        report = PerformanceAnalyzer(self.j).report(1)
        self.assertEqual(report['legacy_estimated_trades_excluded'], 1)
        self.assertEqual(report['total_profit_loss'], 0)


class BotRepairTests(unittest.TestCase):
    def setUp(self):
        self.guardian_patch = patch.object(bot, 'guardian')
        self.guardian = self.guardian_patch.start()
        self.guardian.entry_allowed.return_value = True
        self.addCleanup(self.guardian_patch.stop)
    def test_recent_bars_drops_incomplete_bar_and_rejects_stale_data(self):
        import pandas as pd
        now = datetime(2026, 9, 21, 15, 0, 30, tzinfo=timezone.utc)
        bars = make_bars([100]*31)
        bars.index = pd.date_range('2026-09-21 14:30', periods=31, freq='min', tz='UTC')
        with patch.object(bot, 'datetime') as clock, patch.object(bot.data, 'get_stock_bars', return_value=NS(df=bars)):
            clock.now.return_value = now
            clean = bot.recent_bars('AMD')
            self.assertEqual(clean.index[-1], pd.Timestamp('2026-09-21 14:59', tz='UTC'))
            clock.now.return_value = now + timedelta(minutes=10)
            self.assertIsNone(bot.recent_bars('AMD'))

    def test_short_history_available_for_position_exits_only(self):
        import pandas as pd
        bars = make_bars([100]*3)
        bars.index = pd.date_range('2026-09-21 13:30', periods=3, freq='min', tz='UTC')
        with patch.object(bot, 'datetime') as clock, patch.object(bot.data, 'get_stock_bars', return_value=NS(df=bars)):
            clock.now.return_value = datetime(2026, 9, 21, 13, 33, 10, tzinfo=timezone.utc)
            self.assertIsNone(bot.recent_bars('AMD'))
            self.assertEqual(len(bot.recent_bars('AMD', minimum_bars=2)), 3)

    def test_daily_limit_still_manages_exits(self):
        for pnl in (-11, 11):
            with self.subTest(pnl=pnl), patch.object(bot, 'execution') as execution, \
                 patch.object(bot, 'market_is_open', return_value=True), \
                 patch.object(bot, 'positions', return_value={'AMD': NS()}), \
                 patch.object(bot, 'open_orders', return_value=[]), \
                 patch.object(bot.market_data_agent, 'fetch', return_value=MarketSnapshot()), \
                 patch.object(bot, 'manage_positions') as manage, \
                 patch.object(bot, 'account_daily_pnl', return_value=pnl), \
                 patch.object(bot, 'submit_buy') as buy:
                execution.reconcile.return_value = True
                execution.pending.return_value = []
                bot.run_cycle()
                manage.assert_called_once()
                buy.assert_not_called()

    def test_one_exit_failure_does_not_skip_other_positions(self):
        bars = make_bars([99]*30)
        positions = {s: NS(avg_entry_price=100, qty=1) for s in ('AMD', 'TSM')}
        with patch.object(bot.execution, 'pending', return_value=[]), \
             patch.object(bot, 'submit_sell', side_effect=[RuntimeError('outage'), NS(id='sell')]) as sell, \
             patch.object(bot.journal, 'record_cycle'):
            bot.manage_positions(positions, {s: bars for s in positions}, [])
        self.assertEqual(sell.call_count, 2)

    def test_pending_orders_block_new_entries(self):
        with patch.object(bot, 'execution') as execution, \
             patch.object(bot, 'market_is_open', return_value=True), \
             patch.object(bot, 'positions', return_value={}), \
             patch.object(bot, 'open_orders', return_value=[NS(symbol='AMD')]), \
             patch.object(bot.market_data_agent, 'fetch', return_value=MarketSnapshot()), \
             patch.object(bot, 'account_daily_pnl', return_value=0), \
             patch.object(bot, 'submit_buy') as buy:
            execution.reconcile.return_value = True
            execution.pending.return_value = []
            bot.run_cycle()
            buy.assert_not_called()


class ResearchRepairTests(unittest.TestCase):
    def test_backtest_requires_dip_even_when_score_passes(self):
        result = walk_forward_backtest(make_bars(list(range(100, 150))), minimum_score=0)
        self.assertEqual(result['trades'], 0)

    def test_costs_reduce_backtest_returns(self):
        bars = make_bars([100]*30 + [98,99,101,102,100,99,101])
        free = walk_forward_backtest(bars, minimum_score=0, slippage_bps=0)
        cost = walk_forward_backtest(bars, minimum_score=0, slippage_bps=10)
        self.assertLess(cost['ending_cash'], free['ending_cash'])

    def test_flat_rsi_is_neutral_and_invalid_data_rejected(self):
        self.assertEqual(score_signal(make_bars([100]*30)).rsi, 50)
        with self.assertRaises(ValueError):
            score_signal(make_bars([100]*29 + [float('nan')]))
        bars = make_bars([100]*30)
        bars['volume'] = 0
        with self.assertRaises(ValueError):
            score_signal(bars)

    def test_holdout_cannot_choose_winner(self):
        current = ResearchCandidate(.0035, 60, .0045, .005)
        other = ResearchCandidate(.005, 80, .0045, .005)
        agent = AfterHoursLearningAgent(minimum_test_trades=3)
        def result(ret):
            return dict(trades=10, return_pct=ret, win_rate_pct=50)
        with patch.object(agent, 'candidates', return_value=[current, other]), \
             patch('after_hours_agent.walk_forward_backtest', side_effect=[result(10), result(-5), result(1), result(100)]):
            report = agent.study_symbol('AMD', make_bars([100]*200), current)
        self.assertEqual(report['best_candidate']['parameters']['minimum_score'], 60)


if __name__ == '__main__':
    unittest.main()
