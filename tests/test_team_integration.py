import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import patch

os.environ.setdefault('APCA_API_KEY_ID', 'test-key')
os.environ.setdefault('APCA_API_SECRET_KEY', 'test-secret')
os.environ.setdefault('JOURNAL_DB_PATH', tempfile.mktemp(suffix='.db'))
import bot
from market_data_agent import MarketSnapshot
from test_strategy import make_bars


class TeamIntegrationTests(unittest.TestCase):
    def test_guardian_blocks_entries_after_exit_management(self):
        with patch.object(bot, 'guardian') as guardian, \
             patch.object(bot.execution, 'reconcile', return_value=True), \
             patch.object(bot, 'market_is_open', return_value=True), \
             patch.object(bot, 'positions', return_value={}), \
             patch.object(bot, 'open_orders', return_value=[]), \
             patch.object(bot.market_data_agent, 'fetch', return_value=MarketSnapshot()), \
             patch.object(bot, 'manage_positions', return_value=True) as exits, \
             patch.object(bot, 'submit_buy') as buy:
            guardian.entry_allowed.return_value = False
            self.assertTrue(bot.run_cycle())
            exits.assert_called_once()
            buy.assert_not_called()

    def test_invalid_position_marks_exit_management_unhealthy(self):
        for entry, qty in [(float('nan'),1), (100,float('nan')), (100,-1), (0,1)]:
            with self.subTest(entry=entry, qty=qty), \
                 patch.object(bot, 'guardian') as guardian, \
                 patch.object(bot.execution, 'pending', return_value=[]), \
                 patch.object(bot.exit_agent, 'decide') as decide:
                self.assertFalse(bot.manage_positions({'AMD': NS(avg_entry_price=entry, qty=qty)},
                                                     {'AMD': make_bars([100]*30)}, []))
                decide.assert_not_called()
                guardian.record_failure.assert_called_once_with('exit_management')

    def test_clock_outage_uses_only_recent_verified_session(self):
        now = datetime.now(timezone.utc)
        with patch.object(bot, '_verified_open_until', now+timedelta(seconds=30)), \
             patch.object(bot, '_clock_degraded', False), patch.object(bot, 'guardian'), \
             patch.object(bot.trading, 'get_clock', side_effect=TimeoutError()):
            self.assertTrue(bot.market_is_open())
            self.assertTrue(bot._clock_degraded)
        with patch.object(bot, '_verified_open_until', now-timedelta(seconds=1)), \
             patch.object(bot, '_clock_degraded', False), patch.object(bot, 'guardian'), \
             patch.object(bot.trading, 'get_clock', side_effect=TimeoutError()):
            self.assertIsNone(bot.market_is_open())

    def test_unknown_clock_does_not_start_research(self):
        with patch.object(bot, 'account_equity', return_value=100), \
             patch.object(bot, 'run_cycle', return_value=None), patch.object(bot, 'guardian'), \
             patch.object(bot, 'run_after_hours_learning') as research, \
             patch.object(bot.time, 'sleep', side_effect=KeyboardInterrupt):
            bot.run()
            research.assert_not_called()


if __name__ == '__main__':
    unittest.main()
