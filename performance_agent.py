"""Advisory-only agent that studies paper results and suggests rule reviews."""

from journal import PerformanceAnalyzer, TradeJournal


class PerformanceAgent:
    """Analyze the journal without changing trading configuration."""

    def __init__(self, journal_or_path, minimum_sample=10):
        self.journal = (journal_or_path if isinstance(journal_or_path, TradeJournal)
                        else TradeJournal(journal_or_path))
        self.minimum_sample = int(minimum_sample)

    def analyze(self, days=7, save=True):
        report = PerformanceAnalyzer(self.journal).report(days)
        recommendations = self.recommend(report)
        result = {"report": report, "recommendations": recommendations}
        if save:
            self.journal.record_performance_snapshot(
                period_days=days, report=report,
                recommendations=recommendations,
            )
        return result

    def recommend(self, report):
        closed = report["wins"] + report["losses"]
        advice = []
        if closed < self.minimum_sample:
            advice.append(
                f"Keep current rules unchanged until at least {self.minimum_sample} "
                f"closed paper trades are recorded (currently {closed})."
            )
            return advice

        if report["expectancy_per_trade"] <= 0:
            advice.append("Pause new entries for review: expectancy is not positive.")
        if report["win_rate_pct"] < 45:
            advice.append("Review raising MIN_SIGNAL_SCORE; the paper win rate is below 45%.")
        if report["profit_factor"] < 1:
            advice.append("Review entry and exit rules; gross paper losses exceed gross wins.")
        if report["maximum_drawdown"] > abs(report["total_profit_loss"]):
            advice.append("Review smaller trade size; drawdown exceeds net paper profit.")

        weak_symbols = [
            symbol for symbol, values in report["by_symbol"].items()
            if values["trades"] >= 3 and values["total_profit_loss"] < 0
        ]
        if weak_symbols:
            advice.append("Review or temporarily exclude weak paper symbols: " +
                          ", ".join(weak_symbols) + ".")

        weak_scores = [
            score_range for score_range, values in report["by_score_range"].items()
            if values["trades"] >= 3 and values["total_profit_loss"] < 0
        ]
        if weak_scores:
            advice.append("Review losing signal-score ranges: " +
                          ", ".join(weak_scores) + ".")
        if not advice:
            advice.append("Current paper rules are performing acceptably; collect more data before changing them.")
        return advice

    @staticmethod
    def log_summary(result):
        report = result["report"]
        print(
            f"[PERFORMANCE] {report['days']}d closed={report['wins'] + report['losses']} "
            f"win_rate={report['win_rate_pct']:.1f}% "
            f"P/L=${report['total_profit_loss']:.2f} "
            f"expectancy=${report['expectancy_per_trade']:.2f} "
            f"drawdown=${report['maximum_drawdown']:.2f}"
        )
        for recommendation in result["recommendations"]:
            print(f"[PERFORMANCE ADVICE] {recommendation}")
