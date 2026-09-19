"""Durable operational circuit breaker. Its decisions only gate NEW entries.

Exit management must always run independently of this agent.
"""

import json
import math
import time
from pathlib import Path


class GuardianAgent:
    def __init__(self, connection, failure_threshold=3, cooldown_seconds=300,
                 clock=time.time):
        if int(failure_threshold) != failure_threshold or failure_threshold < 1:
            raise ValueError("failure_threshold must be a positive integer")
        if not math.isfinite(float(cooldown_seconds)) or cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be positive and finite")
        self.connection = connection
        self.failure_threshold = int(failure_threshold)
        self.cooldown_seconds = float(cooldown_seconds)
        self.clock = clock
        self.connection.execute("""CREATE TABLE IF NOT EXISTS guardian_health (
            category TEXT PRIMARY KEY, failures INTEGER NOT NULL,
            blocked_until REAL NOT NULL, updated_at REAL NOT NULL)""")
        self.connection.commit()

    def record_failure(self, category):
        if not isinstance(category, str) or not category.strip():
            raise ValueError("category must be a nonempty string")
        now = self.clock()
        with self.connection:
            row = self.connection.execute(
                "SELECT failures, blocked_until FROM guardian_health WHERE category=?",
                (category,)).fetchone()
            failures = (row[0] if row else 0) + 1
            until = row[1] if row else 0.0
            if failures >= self.failure_threshold:
                until = now + self.cooldown_seconds
            self.connection.execute("""INSERT OR REPLACE INTO guardian_health
                (category, failures, blocked_until, updated_at) VALUES (?, ?, ?, ?)""",
                (category, failures, until, now))

    def record_success(self, category):
        # Reset only this subsystem. A quote success cannot hide broker failures.
        # An already-open circuit still serves its complete cooling-off period.
        with self.connection:
            self.connection.execute("""UPDATE guardian_health SET failures=0,
                updated_at=? WHERE category=?""", (self.clock(), category))

    def entry_allowed(self):
        return not self.connection.execute(
            "SELECT 1 FROM guardian_health WHERE blocked_until > ? LIMIT 1",
            (self.clock(),)).fetchone()

    def status(self):
        now = self.clock()
        rows = self.connection.execute("""SELECT category, failures,
            blocked_until, updated_at FROM guardian_health ORDER BY category""").fetchall()
        categories = {row[0]: dict(consecutive_failures=row[1], blocked_until=row[2],
                                  updated_at=row[3], blocked=row[2] > now)
                      for row in rows}
        return dict(entry_allowed=not any(item["blocked"] for item in categories.values()),
                    exits_allowed=True, categories=categories, timestamp=now)

    def write_status(self, path):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(self.status(), indent=2), encoding="utf-8")
        temporary.replace(target)
