import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

from journal import TradeJournal
from dashboard_feed import dashboard_snapshot, make_handler


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'journal.db'
        journal=TradeJournal(str(self.path))
        now=datetime.now(timezone.utc).isoformat()
        journal.record_trade(symbol='AMD',score=80,quantity=.25,entry_price=100,
                             exit_price=102,realized_pnl=.5,status='CLOSED',closed_at=now)
        journal.connection.execute("INSERT INTO paper_trades (symbol,score,status,quantity,realized_pnl,opened_at,closed_at) VALUES ('OLD',60,'CLOSED',1,999,?,?)",(now,now))
        journal.connection.commit()
        journal.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_only_verified_pnl_and_no_database_changes(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        result=dashboard_snapshot(str(self.path))
        self.assertEqual(result['metrics']['realized_pnl'],.5)
        self.assertEqual(result['metrics']['legacy_excluded'],1)
        self.assertEqual(result['metrics']['closed_trades'],1)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
        self.assertTrue(result['read_only'])
        self.assertEqual(result['mode'],'PAPER')

    def test_missing_journal_is_not_created(self):
        absent=Path(self.temp.name)/'absent.db'
        with self.assertRaises(sqlite3.Error):
            dashboard_snapshot(str(absent))
        self.assertFalse(absent.exists())

    def test_no_closed_trades_is_unknown_pnl_not_fake_zero(self):
        other=Path(self.temp.name)/'empty.db'
        TradeJournal(str(other)).close()
        result=dashboard_snapshot(str(other))
        self.assertIsNone(result['metrics']['realized_pnl'])
        self.assertIsNone(result['metrics']['win_rate'])

    def test_authenticated_get_only_and_no_secret_in_payload(self):
        token='test-only-token-'+('x'*32)
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(str(self.path),token))
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        url=f'http://127.0.0.1:{server.server_address[1]}/api/dashboard'
        try:
            with self.assertRaises(HTTPError) as missing:
                urlopen(url)
            self.assertEqual(missing.exception.code,401)
            with urlopen(Request(url,headers={'Authorization':'Bearer '+token})) as response:
                payload=response.read().decode()
                self.assertTrue(json.loads(payload)['read_only'])
                self.assertNotIn(token,payload)
            with self.assertRaises(HTTPError) as write:
                urlopen(Request(url,method='POST',headers={'Authorization':'Bearer '+token}))
            self.assertEqual(write.exception.code,501)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_period_validation(self):
        with self.assertRaises(ValueError):
            dashboard_snapshot(str(self.path),365)


if __name__=='__main__':
    unittest.main()
