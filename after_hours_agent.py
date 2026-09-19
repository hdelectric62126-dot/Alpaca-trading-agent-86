"""Advisory-only after-hours strategy research agent.

This module never imports the trading client and cannot submit orders. It tests a
small, bounded set of parameter variations on historical bars, validates them on
newer unseen bars, and writes recommendations for a human to review.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path

import pandas as pd

from strategy import walk_forward_backtest
from research_validation_agent import ResearchValidationAgent


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
        self.minimum_test_trades = max(1, int(minimum_test_trades))
        self.validator = ResearchValidationAgent(self.minimum_test_trades)

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

    @staticmethod
    def _valid_statistics(result):
        try:
            valid = all(math.isfinite(float(result[field]))
                        for field in ("trades", "return_pct", "win_rate_pct"))
            json.dumps(result, allow_nan=False)
            return valid
        except (ValueError, TypeError, KeyError, OverflowError):
            return False

    def study_symbol(self, symbol, bars, current):
        if len(bars) < 120:
            return {"symbol": symbol, "status": "insufficient_data", "bars": len(bars)}

        split = max(60, int(len(bars) * 0.70))
        train = bars.iloc[:split].copy()
        test = bars.iloc[split:].copy()
        results = []
        candidate_count = 0
        discarded_candidates = 0
        for candidate in self.candidates(current):
            candidate_count += 1
            kwargs = asdict(candidate)
            train_result = walk_forward_backtest(train, **kwargs)
            if not self._valid_statistics(train_result):
                discarded_candidates += 1
                continue
            results.append({
                "parameters": kwargs,
                "train": train_result,
            })

        if not results:
            return {"symbol": symbol, "status": "invalid_data", "bars": len(bars),
                    "reason": "No candidate produced finite training statistics",
                    "candidate_count": candidate_count}

        # Select only on training data; never shop for the best holdout result.
        eligible = [result for result in results if result['train']['trades'] >= self.minimum_test_trades]
        ranked = sorted(
            eligible or results,
            key=lambda result: (
                -result["train"]["return_pct"],
                -result["train"]["win_rate_pct"],
                json.dumps(result["parameters"], sort_keys=True),
            ),
        )
        selected = ranked[0]
        selected["test"] = walk_forward_backtest(test, **selected["parameters"])
        baseline = (selected["test"] if selected["parameters"] == asdict(current)
                    else walk_forward_backtest(test, **asdict(current)))
        if not all(self._valid_statistics(item) for item in (selected["test"], baseline)):
            return {"symbol": symbol, "status": "invalid_data", "bars": len(bars),
                    "reason": "Selected candidate or baseline produced invalid holdout statistics"}
        evidence = self.validator.assess(selected, baseline)
        return {
            "symbol": symbol,
            "status": "studied",
            "bars": len(bars),
            "best_candidate": ranked[0],
            "validation_qualified": evidence["qualified_for_paper_review"],
            "evidence_review": evidence,
            "baseline_holdout": baseline,
            "current_parameters": asdict(current),
            "candidate_count": candidate_count,
            "discarded_candidates": discarded_candidates,
        }

    def run(self, bars_by_symbol, current):
        studies = []
        for symbol, bars in sorted(bars_by_symbol.items()):
            try:
                studies.append(self.study_symbol(symbol, bars, current))
            except Exception as exc:
                # One bad symbol must not discard evidence from the rest of the batch.
                studies.append({"symbol": symbol, "status": "invalid_data",
                                "reason": f"{type(exc).__name__}: {str(exc)[:160]}"})
        studied = [item for item in studies if item["status"] == "studied"]
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "ADVISORY_ONLY",
            "automatic_rule_changes": False,
            "automatic_orders": False,
            "approval_required": True,
            "method": "70/30 chronological split; select on training only, evaluate selected candidate on holdout; hourly proxy with slippage, not live-strategy validation",
            "studies": studies,
            "recommendation": self._recommend(studied, current),
        }
        self.save(report)
        return report

    @staticmethod
    def _recommend(studies, current):
        if not studies:
            return "No recommendation: not enough historical data. Keep current rules."
        winners = [item["best_candidate"] for item in studies if item.get('validation_qualified', False)]
        positive = [item for item in winners if item["test"]["return_pct"] > 0]
        if len(positive) < max(2, len(studies) // 2):
            return "No rule change recommended: results were not positive across enough symbols."

        parameter_sets = [json.dumps(item["parameters"], sort_keys=True) for item in positive]
        most_common = sorted(set(parameter_sets), key=lambda value: (-parameter_sets.count(value), value))[0]
        if parameter_sets.count(most_common) < max(2, (len(studies) + 1) // 2):
            return "No rule change recommended: qualifying symbols disagree on parameters."
        proposed = json.loads(most_common)
        if proposed == asdict(current):
            return "Hourly proxy favors current parameters; continue collecting minute-level paper evidence."
        return (
            "Further minute-level paper testing suggested for these hourly-proxy parameters: "
            f"{proposed}. Do not apply without Daniel's approval."
        )

    def save(self, report):
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.output_path.with_suffix(self.output_path.suffix + ".tmp")
            temp_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
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

