"""Deterministic, paper-only position exit decisions."""

from dataclasses import dataclass

from strategy import _rsi


@dataclass(frozen=True)
class ExitDecision:
    action: str
    reason: str
    pnl_pct: float


class ExitAgent:
    """Protect losses and manage profitable exits using price behavior."""

    def __init__(self, take_profit_pct, stop_loss_pct,
                 trailing_arm_pct=0.35 / 100, trailing_gap_pct=0.20 / 100,
                 extended_profit_multiple=1.5, connection=None):
        self.take_profit_pct = float(take_profit_pct)
        self.stop_loss_pct = float(stop_loss_pct)
        self.trailing_arm_pct = float(trailing_arm_pct)
        self.trailing_gap_pct = float(trailing_gap_pct)
        self.extended_profit_multiple = float(extended_profit_multiple)
        self.high_water = {}
        self.entry_identity = {}
        self.connection = connection
        if connection is not None:
            connection.execute("""CREATE TABLE IF NOT EXISTS exit_high_water (
                symbol TEXT PRIMARY KEY, entry_identity TEXT NOT NULL,
                peak REAL NOT NULL)""")
            connection.commit()
            for symbol, identity, peak in connection.execute(
                    "SELECT symbol, entry_identity, peak FROM exit_high_water"):
                self.high_water[symbol] = peak
                self.entry_identity[symbol] = identity

    def clear(self, symbol):
        self.high_water.pop(symbol, None)
        self.entry_identity.pop(symbol, None)
        if self.connection is not None:
            with self.connection:
                self.connection.execute("DELETE FROM exit_high_water WHERE symbol=?", (symbol,))

    def decide(self, symbol, entry_price, bars, entry_identity=None):
        price = float(bars["close"].iloc[-1])
        entry = float(entry_price)
        identity = repr((entry, str(entry_identity) if entry_identity is not None else None))
        if self.entry_identity.get(symbol) != identity:
            self.high_water.pop(symbol, None)
        self.entry_identity[symbol] = identity
        pnl_pct = (price - entry) / entry
        peak = max(price, self.high_water.get(symbol, price))
        self.high_water[symbol] = peak
        if self.connection is not None:
            with self.connection:
                self.connection.execute("""INSERT OR REPLACE INTO exit_high_water
                    (symbol, entry_identity, peak) VALUES (?, ?, ?)""", (symbol, identity, peak))
        peak_gain_pct = (peak - entry) / entry
        pullback_pct = (peak - price) / peak

        # This hard loss boundary can never be relaxed by the smart rules.
        if pnl_pct <= -self.stop_loss_pct:
            return ExitDecision("SELL", "hard stop loss", pnl_pct)

        extended_target = self.take_profit_pct * self.extended_profit_multiple
        if pnl_pct >= extended_target:
            return ExitDecision("SELL", "extended profit target", pnl_pct)

        if (peak_gain_pct >= self.trailing_arm_pct and
                pullback_pct >= self.trailing_gap_pct):
            return ExitDecision("SELL", "trailing profit protection", pnl_pct)

        closes = bars["close"].astype(float)
        short_momentum = ((closes.iloc[-1] / closes.iloc[-4]) - 1
                          if len(closes) >= 4 else 0.0)
        rsi = float(_rsi(closes))
        if pnl_pct >= self.take_profit_pct and (short_momentum <= 0 or rsi >= 70):
            reason = "take profit on fading momentum" if short_momentum <= 0 else "take profit on overbought RSI"
            return ExitDecision("SELL", reason, pnl_pct)

        if peak_gain_pct >= self.trailing_arm_pct:
            return ExitDecision("HOLD", "profit protection armed", pnl_pct)
        return ExitDecision("HOLD", "exit conditions not met", pnl_pct)
