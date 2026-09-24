"""Hard, deterministic risk approval for new paper positions."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    notional: float
    reason: str


class RiskAgent:
    def __init__(self, *, minimum_score, max_trade_notional,
                 max_total_exposure, max_open_positions,
                 daily_profit_target, daily_loss_limit,
                 paper_bankroll=500.0):
        self.minimum_score = int(minimum_score)
        self.max_trade_notional = float(max_trade_notional)
        self.max_total_exposure = float(max_total_exposure)
        self.max_open_positions = int(max_open_positions)
        self.daily_profit_target = float(daily_profit_target)
        self.daily_loss_limit = float(daily_loss_limit)
        self.paper_bankroll = float(paper_bankroll)

    def assess(self, *, score, positions, daily_pnl, has_open_order=False):
        values = [daily_pnl, score, self.max_trade_notional, self.max_total_exposure,
                  self.daily_profit_target, self.daily_loss_limit, self.paper_bankroll]
        if not all(math.isfinite(float(v)) for v in values):
            return RiskDecision(False, 0.0, 'invalid risk inputs')
        if self.paper_bankroll <= 0:
            return RiskDecision(False, 0.0, 'invalid paper bankroll')
        if any(not math.isfinite(float(getattr(p, 'market_value', float('nan')))) for p in positions.values()):
            return RiskDecision(False, 0.0, 'invalid portfolio exposure')
        if daily_pnl >= self.daily_profit_target:
            return RiskDecision(False, 0.0, "daily profit target reached")
        if daily_pnl <= -self.daily_loss_limit:
            return RiskDecision(False, 0.0, "daily loss limit reached")
        if has_open_order:
            return RiskDecision(False, 0.0, "open order already exists")
        if int(score) < self.minimum_score:
            return RiskDecision(False, 0.0, "signal score below minimum")
        if len(positions) >= self.max_open_positions:
            return RiskDecision(False, 0.0, "maximum open positions reached")

        exposure = sum(abs(float(getattr(position, "market_value", 0) or 0))
                       for position in positions.values())
        effective_equity = max(0.0, self.paper_bankroll + float(daily_pnl))
        available = max(0.0, min(self.max_total_exposure - exposure,
                                 effective_equity - exposure))
        if effective_equity < 1.0:
            return RiskDecision(False, 0.0, "virtual paper bankroll exhausted")
        if available < 1.0:
            return RiskDecision(False, 0.0, "portfolio exposure limit reached")

        # Stronger scores may use more of the fixed cap; no score can exceed it.
        score_span = max(1, 100 - self.minimum_score)
        confidence = min(1.0, max(0.5, 0.5 +
                         (int(score) - self.minimum_score) / score_span * 0.5))
        notional = round(min(self.max_trade_notional * confidence, available), 2)
        if notional < 1.0:
            return RiskDecision(False, 0.0, "approved size is below $1")
        return RiskDecision(True, notional, "entry approved within risk limits")
