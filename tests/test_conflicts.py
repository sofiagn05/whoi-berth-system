import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.conflicts import check_fit, date_ranges_overlap


def test_overlap_true_when_ranges_share_a_day():
    assert date_ranges_overlap(date(2026, 6, 1), date(2026, 6, 10), date(2026, 6, 10), date(2026, 6, 15))


def test_overlap_false_when_ranges_are_adjacent_not_touching():
    assert not date_ranges_overlap(date(2026, 6, 1), date(2026, 6, 9), date(2026, 6, 10), date(2026, 6, 15))


def test_overlap_true_when_one_range_contains_the_other():
    assert date_ranges_overlap(date(2026, 6, 1), date(2026, 6, 30), date(2026, 6, 10), date(2026, 6, 15))


def test_fit_error_when_vessel_longer_than_berth():
    issues = check_fit(berth_length_ft=30, vessel_loa_ft=33)
    assert any(i.severity == "error" for i in issues)


def test_fit_warning_when_under_10_percent_margin():
    issues = check_fit(berth_length_ft=31, vessel_loa_ft=30)
    assert issues and issues[0].severity == "warning"


def test_fit_clean_with_healthy_margin():
    issues = check_fit(berth_length_ft=40, vessel_loa_ft=30)
    assert issues == []


def test_fit_error_on_beam_and_draft():
    issues = check_fit(
        berth_length_ft=50, vessel_loa_ft=30,
        berth_max_beam_ft=10, vessel_beam_ft=12,
        berth_max_draft_ft=5, vessel_draft_ft=8,
    )
    messages = [i.message for i in issues]
    assert any("beam" in m for m in messages)
    assert any("draft" in m for m in messages)
