"""Paper-only sizing profiles.

Profiles change exposure only. They do not change the signal, dip, score,
or exit logic, so paper results remain comparable.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class PaperSizing:
    max_trade_notional: float
    max_total_exposure: float
    max_open_positions: int


def resolve_paper_sizing(profile, *, max_trade_notional, max_total_exposure, max_open_positions):
    name = str(profile or "baseline").strip().lower()
    baseline = PaperSizing(
        float(max_trade_notional),
        float(max_total_exposure),
        int(max_open_positions),
    )
    if name == "baseline":
        return baseline
    if name == "size2x":
        # Deliberately modest: doubles the per-trade cap but does not allow
        # more simultaneous positions or unrestricted portfolio exposure.
        return PaperSizing(
            max_trade_notional=min(baseline.max_trade_notional * 2.0, 50.0),
            max_total_exposure=min(baseline.max_total_exposure + 25.0, 100.0),
            max_open_positions=min(baseline.max_open_positions, 3),
        )
    raise ValueError("PAPER_TUNING_PROFILE must be 'baseline' or 'size2x'")
