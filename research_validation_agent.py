"""Independent evidence gate; proxy results never authorize trading changes."""
import math


class ResearchValidationAgent:
    def __init__(self, minimum_trades=3):
        self.minimum_trades = max(1, int(minimum_trades))

    def assess(self, candidate, baseline):
        reasons = []
        for name, result in (("training", candidate["train"]),
                             ("holdout", candidate["test"]),
                             ("baseline_holdout", baseline)):
            if result.get("trades", 0) < self.minimum_trades:
                reasons.append(f"{name}: insufficient completed trades")
            if not all(math.isfinite(float(result.get(field, float('nan'))))
                       for field in ("return_pct", "win_rate_pct", "trades")):
                reasons.append(f"{name}: invalid statistics")
        improvement = candidate["test"]["return_pct"] - baseline["return_pct"]
        if candidate["test"]["return_pct"] <= 0:
            reasons.append("holdout: non-positive return after modeled costs")
        if improvement <= 0:
            reasons.append("holdout: no improvement over current parameters")
        return {
            "agent": "ResearchValidationAgent",
            "qualified_for_paper_review": not reasons,
            "live_strategy_validated": False,
            "return_improvement_percentage_points": improvement,
            "minimum_completed_trades": self.minimum_trades,
            "reasons": reasons,
            "limitations": ["Hourly proxy does not validate minute strategy execution",
                            "Small chronological holdout is not statistical proof",
                            "Repeated research on the same holdout can overfit"],
        }
