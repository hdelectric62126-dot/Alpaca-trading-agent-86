"""Advisory-only after-hours strategy research agent.

This module never imports the trading client and cannot submit orders. It tests a
small, bounded set of parameter variations on historical bars, validates them on
newer unseen bars, and writes recommendations for a human to review.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pandas as pd

from strategy import walk_forward_backtest


@dataclass(frozen=True)
class ResearchCandidate:
    dip_threshold: float
    minimum_score: int
    take_profit: float
    stop_loss: float


class AfterHoursLearningAgent:
    """Compare strategy variants without changing or executing trading rules."""

    def __init__(self, output_path=None, minimum_test_trades=3):
        self.output_path = Path(output_path or os.getenv(
            "AFTER_HOURS_REPORT_PATH", "/data/after_hours_recommendations.json"
        ))
        self.minimum_test_trades = int(minimum_test_trades)

    @staticmethod
    def candidates(current):
        dips = sorted({current.dip_threshold, 0.0025, 0.0035, 0.0050})
        scores = sorted({current.minimum_score, 60, 80})
        exits = sorted({
            (current.take_profit, current.stop_loss),
            (0.0035, 0.0040),
            (0.0045, 0.0050),
            (0.0060, 0.0050),
        })
        return [
            ResearchCandidate(dip, score, take_profit, stop_loss)
            for dip in dips
            for score in scores
            for take_profit, stop_loss in exits
        ]

    def study_symbol(self, symbol, bars, current):
        if len(bars) < 120:
            return {"symbol": symbol, "status": "insufficient_data", "bars": len(bars)}

        split = max(60, int(len(bars) * 0.70))
        train = bars.iloc[:split].copy()
        test = bars.iloc[split:].copy()
        results = []
        for candidate in self.candidates(current):
            kwargs = asdict(candidate)
            train_result = walk_forward_backtest(train, **kwargs)
            test_result = walk_forward_backtest(test, **kwargs)
            results.append({
                "parameters": kwargs,
                "train": train_result,
                "test": test_result,
            })

        eligible = [
            result for result in results
            if result["test"]["trades"] >= self.minimum_test_trades
        ]
        ranked = sorted(
            eligible or results,
            key=lambda result: (
                result["test"]["return_pct"],
                result["test"]["win_rate_pct"],
                result["train"]["return_pct"],
            ),
            reverse=True,
        )
        return {
            "symbol": symbol,
            "status": "studied",
            "bars": len(bars),
            "best_candidate": ranked[0],
            "current_parameters": asdict(current),
            "candidate_count": len(results),
        }

    def run(self, bars_by_symbol, current):
        studies = [
            self.study_symbol(symbol, bars, current)
            for symbol, bars in sorted(bars_by_symbol.items())
        ]
        studied = [item for item in studies if item["status"] == "studied"]
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "ADVISORY_ONLY",
            "automatic_rule_changes": False,
            "automatic_orders": False,
            "approval_required": True,
            "method": "70/30 chronological train/test walk-forward comparison",
            "studies": studies,
            "recommendation": self._recommend(studied, current),
        }
        self.save(report)
        return report

    @staticmethod
    def _recommend(studies, current):
        if not studies:
            return "No recommendation: not enough historical data. Keep current rules."
        winners = [item["best_candidate"] for item in studies]
        positive = [item for item in winners if item["test"]["return_pct"] > 0]
        if len(positive) < max(2, len(studies) // 2):
            return "No rule change recommended: results were not positive across enough symbols."

        parameter_sets = [json.dumps(item["parameters"], sort_keys=True) for item in positive]
        most_common = max(set(parameter_sets), key=parameter_sets.count)
        proposed = json.loads(most_common)
        if proposed == asdict(current):
            return "Historical validation supports the current rules; keep collecting paper results."
        return (
            "Human review suggested for these paper-only parameters: "
            f"{proposed}. Do not apply without Daniel's approval."
        )

    def save(self, report):
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.output_path.with_suffix(self.output_path.suffix + ".tmp")
            temp_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            temp_path.replace(self.output_path)
        except OSError as exc:
            print(f"[AFTER HOURS] Could not save report: {exc}")

    @staticmethod
    def log_summary(report):
        completed = sum(item["status"] == "studied" for item in report["studies"])
        print(
            f"[AFTER HOURS] completed advisory research for {completed}/"
            f"{len(report['studies'])} symbols"
        )
        print(f"[AFTER HOURS ADVICE] {report['recommendation']}")

