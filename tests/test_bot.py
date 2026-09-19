import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("APCA_API_KEY_ID", "test-key")
os.environ.setdefault("APCA_API_SECRET_KEY", "test-secret")
os.environ.setdefault("PAPER_ONLY", "true")
os.environ.setdefault("JOURNAL_DB_PATH", tempfile.mktemp(suffix=".db"))

import bot


class BotTests(unittest.TestCase):
    def test_recent_bars_isolates_symbol_data_failures(self):
        with patch.object(bot.data, "get_stock_bars", side_effect=RuntimeError("temporary outage")):
            self.assertIsNone(bot.recent_bars("AMD"))

    def test_account_daily_pnl_uses_alpaca_last_equity(self):
        account = SimpleNamespace(equity="105.50", last_equity="100.00")
        with patch.object(bot.trading, "get_account", return_value=account):
            self.assertEqual(bot.account_daily_pnl(), 5.50)

    def test_run_does_not_recalculate_pnl_from_startup_equity(self):
        with patch.object(bot, "account_equity", return_value=100.0) as account_equity, \
                patch.object(bot, "account_daily_pnl", return_value=0.0) as daily_pnl, \
                patch.object(bot, "market_is_open", return_value=True), \
                patch.object(bot, "daily_summary"), \
                patch.object(bot, "positions", return_value={}), \
                patch.object(bot, "open_orders", return_value=[]), \
                patch.object(bot.time, "sleep", side_effect=KeyboardInterrupt), \
                patch.object(bot, "SYMBOLS", []):
            bot.run()

        account_equity.assert_called_once_with()
        daily_pnl.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
