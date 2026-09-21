"""Pure, DB-free logic for date-overlap and physical-fit checks.

Kept separate from the DB/API layers so it can be unit tested directly
and reused by both the live API and the historical-data audit script.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator, Optional

RECOMMENDED_LENGTH_MARGIN = 0.10  # rule of thumb: berth should be >=10% longer than LOA


@dataclass
class DimensionIssue:
    severity: str  # "error" (does not physically fit) or "warning" (fits, but tight/suboptimal)
    message: str


def date_ranges_overlap(start_a: date, end_a: date, start_b: date, end_b: date) -> bool:
    """Inclusive-range overlap check: True if [start_a, end_a] and [start_b, end_b] share a day."""
    return start_a <= end_b and start_b <= end_a


def iter_days(start: date, end: date) -> Iterator[date]:
    """Every calendar day in the inclusive range [start, end]."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def check_fit(
    berth_length_ft: float,
    vessel_loa_ft: Optional[float],
    berth_max_beam_ft: Optional[float] = None,
    vessel_beam_ft: Optional[float] = None,
    berth_max_draft_ft: Optional[float] = None,
    vessel_draft_ft: Optional[float] = None,
) -> list[DimensionIssue]:
    """Check a vessel's dimensions against a berth's limits.

    Returns a list of issues: "error" means the vessel physically does not
    fit (hard block), "warning" means it fits but violates the recommended
    10% length-margin rule of thumb, or that a dimension needed to verify
    fit is missing from the vessel record.
    """
    issues: list[DimensionIssue] = []

    if vessel_loa_ft is None:
        issues.append(DimensionIssue(
            "warning",
            "Vessel LOA is unknown; cannot verify it fits this berth.",
        ))
    elif vessel_loa_ft > berth_length_ft:
        issues.append(DimensionIssue(
            "error",
            f"Vessel LOA {vessel_loa_ft}ft exceeds berth length {berth_length_ft}ft.",
        ))
    else:
        min_recommended = vessel_loa_ft * (1 + RECOMMENDED_LENGTH_MARGIN)
        if berth_length_ft < min_recommended:
            issues.append(DimensionIssue(
                "warning",
                f"Berth length {berth_length_ft}ft gives less than the recommended "
                f"10% margin over vessel LOA {vessel_loa_ft}ft "
                f"(recommended >= {min_recommended:.1f}ft).",
            ))

    if berth_max_beam_ft is not None and vessel_beam_ft is not None:
        if vessel_beam_ft > berth_max_beam_ft:
            issues.append(DimensionIssue(
                "error",
                f"Vessel beam {vessel_beam_ft}ft exceeds berth max beam {berth_max_beam_ft}ft.",
            ))

    if berth_max_draft_ft is not None and vessel_draft_ft is not None:
        if vessel_draft_ft > berth_max_draft_ft:
            issues.append(DimensionIssue(
                "error",
                f"Vessel draft {vessel_draft_ft}ft exceeds berth max draft {berth_max_draft_ft}ft.",
            ))

    return issues
