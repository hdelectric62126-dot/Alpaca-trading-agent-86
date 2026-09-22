import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live_readiness import evaluate


SCHEMA = """
CREATE TABLE paper_trades (
  id INTEGER PRIMARY KEY,
  status TEXT,
  fill_verified INTEGER,
  realized_pnl REAL,
  closed_at TEXT,
  symbol TEXT
);
CREATE TABLE order_intents (status TEXT);
CREATE TABLE guardian_health (
  category TEXT PRIMARY KEY,
  failures INTEGER NOT NULL,
  blocked_until REAL NOT NULL,
  updated_at REAL NOT NULL
);
"""


class LiveReadinessTests(unittest.TestCase):
    def make_db(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        path = Path(handle.name)
        db = sqlite3.connect(path)
        db.executescript(SCHEMA)
        db.close()
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def seed_trades(self, path, count=50, days=10, pnl=0.10):
        db = sqlite3.connect(path)
        now = datetime.now(timezone.utc)
        for index in range(count):
            closed = now - timedelta(days=index % days)
            db.execute(
                "INSERT INTO paper_trades(status,fill_verified,realized_pnl,closed_at,symbol) VALUES ('CLOSED',1,?,?,?)",
                (pnl, closed.isoformat(), "AMD"),
            )
        db.commit()
        db.close()

    def test_good_paper_sample_is_only_ready_for_review(self):
        path = self.make_db()
        self.seed_trades(path)
        result = evaluate(path)
        self.assertTrue(result["ready_for_live_review"])
        self.assertFalse(result["live_trading_enabled"])

    def test_pending_order_blocks_review(self):
        path = self.make_db()
        self.seed_trades(path)
        db = sqlite3.connect(path)
        db.execute("INSERT INTO order_intents(status) VALUES ('pending')")
        db.commit()
        db.close()
        result = evaluate(path)
        self.assertFalse(result["checks"]["no_pending_orders"])
        self.assertFalse(result["ready_for_live_review"])

    def test_guardian_block_blocks_review(self):
        path = self.make_db()
        self.seed_trades(path)
        db = sqlite3.connect(path)
        now = time.time()
        db.execute(
            "INSERT INTO guardian_health(category,failures,blocked_until,updated_at) VALUES (?,?,?,?)",
            ("market_data", 3, now + 300, now),
        )
        db.commit()
        db.close()
        result = evaluate(path)
        self.assertFalse(result["checks"]["guardian_not_blocked"])
        self.assertFalse(result["ready_for_live_review"])

    def test_small_sample_stays_in_paper(self):
        path = self.make_db()
        self.seed_trades(path, count=9, days=2)
        result = evaluate(path)
        self.assertFalse(result["checks"]["verified_closed_trades"])
        self.assertFalse(result["checks"]["distinct_trading_days"])
        self.assertFalse(result["ready_for_live_review"])


if __name__ == "__main__":
    unittest.main()
