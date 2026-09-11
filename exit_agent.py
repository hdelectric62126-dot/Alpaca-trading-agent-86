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
                 extended_profit_multiple=1.5):
        self.take_profit_pct = float(take_profit_pct)
        self.stop_loss_pct = float(stop_loss_pct)
        self.trailing_arm_pct = float(trailing_arm_pct)
        self.trailing_gap_pct = float(trailing_gap_pct)
        self.extended_profit_multiple = float(extended_profit_multiple)
        self.high_water = {}

    def clear(self, symbol):
        self.high_water.pop(symbol, None)

    def decide(self, symbol, entry_price, bars):
        price = float(bars["close"].iloc[-1])
        entry = float(entry_price)
        pnl_pct = (price - entry) / entry
        peak = max(price, self.high_water.get(symbol, price))
        self.high_water[symbol] = peak
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
