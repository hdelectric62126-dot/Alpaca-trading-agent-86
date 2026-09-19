import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
from market_data_agent import MarketDataAgent


def fixture(symbols):
    frames = {}
    for symbol in symbols:
        frames[symbol] = pd.DataFrame({
            'open': [100.]*31, 'high': [101.]*31, 'low': [99.]*31,
            'close': [100.]*31, 'volume': [100.]*31,
        }, index=pd.date_range('2026-09-21 14:30', periods=31, freq='min', tz='UTC'))
    return pd.concat(frames, names=['symbol', 'timestamp'])


class MarketDataTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.agent = MarketDataAgent(self.client)
        self.now = datetime(2026, 9, 21, 15, 0, 30, tzinfo=timezone.utc)

    def test_ten_symbols_one_sdk_request_and_completed_bars(self):
        symbols = ['AMD','TSM','TSLA','NVDA','AAPL','MSFT','AMZN','META','GOOGL','AVGO']
        self.client.get_stock_bars.return_value = SimpleNamespace(df=fixture(symbols))
        result = self.agent.fetch(symbols, now=self.now)
        self.assertEqual(len(result.bars), 10)
        self.assertEqual(result.request_count, 1)
        self.client.get_stock_bars.assert_called_once()
        self.assertEqual(result.bars['AMD'].index[-1].minute, 59)

    def test_bad_symbol_does_not_discard_good_symbols(self):
        bars = fixture(['AMD', 'TSM'])
        bars.loc['AMD', 'high'] = 1
        self.client.get_stock_bars.return_value = SimpleNamespace(df=bars)
        result = self.agent.fetch(['AMD','TSM'], now=self.now)
        self.assertIn('AMD', result.rejected)
        self.assertIn('TSM', result.bars)

    def test_missing_symbol_is_explicit(self):
        self.client.get_stock_bars.return_value = SimpleNamespace(df=fixture(['AMD']))
        result = self.agent.fetch(['AMD','TSM'], now=self.now)
        self.assertEqual(result.rejected['TSM'], 'symbol missing from response')

    def test_transport_failure_is_reported_without_retry_storm(self):
        self.client.get_stock_bars.side_effect = TimeoutError()
        result = self.agent.fetch(['AMD','TSM'], now=self.now)
        self.assertFalse(result.transport_ok)
        self.assertEqual(len(result.rejected), 2)
        self.client.get_stock_bars.assert_called_once()

    def test_ambiguous_symbol_response_fails_closed(self):
        self.client.get_stock_bars.return_value = SimpleNamespace(df=fixture(['AMD']).xs('AMD'))
        result = self.agent.fetch(['AMD','TSM'], now=self.now)
        self.assertEqual(result.bars, {})
        self.assertEqual(len(result.rejected), 2)


if __name__ == '__main__':
    unittest.main()
