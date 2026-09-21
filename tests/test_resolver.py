import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.resolver import weighted_interval_schedule_keep


@dataclass
class FakeBooking:
    id: int
    start_date: date
    end_date: date


def test_no_conflict_keeps_everything():
    bookings = [
        FakeBooking(1, date(2026, 1, 1), date(2026, 1, 5)),
        FakeBooking(2, date(2026, 1, 10), date(2026, 1, 15)),
    ]
    kept, displaced = weighted_interval_schedule_keep(bookings)
    assert {b.id for b in kept} == {1, 2}
    assert displaced == []


def test_prefers_longer_booking_over_shorter_conflicting_one():
    # A 15-day research cruise conflicts with a 1-day community sail day.
    # Plain "maximize count" would keep the 1-day event; weighted scheduling
    # should keep the longer, more disruptive-to-move reservation instead.
    cruise = FakeBooking(1, date(2026, 7, 1), date(2026, 7, 15))
    sail_day = FakeBooking(2, date(2026, 7, 10), date(2026, 7, 10))
    kept, displaced = weighted_interval_schedule_keep([cruise, sail_day])
    assert kept == [cruise]
    assert displaced == [sail_day]


def test_three_way_conflict_picks_best_non_overlapping_combination():
    # A(1-19, 19 days) conflicts with both B(1-12, 12 days) and C(13-20, 8
    # days); B and C don't conflict with each other. Keeping A alone has
    # weight 19; keeping B+C instead has weight 20 -- the DP should find
    # that higher-weight combination rather than just keeping the one
    # longest single booking.
    a = FakeBooking(1, date(2026, 1, 1), date(2026, 1, 19))
    b = FakeBooking(2, date(2026, 1, 1), date(2026, 1, 12))
    c = FakeBooking(3, date(2026, 1, 13), date(2026, 1, 20))
    kept, displaced = weighted_interval_schedule_keep([a, b, c])
    assert {x.id for x in kept} == {2, 3}
    assert {x.id for x in displaced} == {1}


def test_empty_input():
    assert weighted_interval_schedule_keep([]) == ([], [])
