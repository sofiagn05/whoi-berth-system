import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.import_schedule import UNLABELED_BERTH_CODE, classify_occupant, parse_grid_csv


def write_csv(tmp_path, rows):
    path = tmp_path / "schedule.csv"
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    return path


def test_unlabeled_data_row_is_kept_not_dropped(tmp_path):
    # Mirrors the real 1997 sample: a berth row followed by a blank-label
    # row that still has an occupant in it (a berth whose name/length was
    # left blank in the source spreadsheet).
    rows = [
        ["AUGUST 1997"] + [""] * 31,
        [""] + [str(d) for d in range(1, 32)],
        ["South Float East - 90'", "", "", "S/V FAR HORIZON"] + [""] * 28,
        ["", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "R/V Amber Tide"] + [""] * 16,
        [""] * 32,
    ]
    path = write_csv(tmp_path, rows)
    entries = parse_grid_csv(path)

    unlabeled = [e for e in entries if e.berth_code == UNLABELED_BERTH_CODE]
    assert len(unlabeled) == 1
    assert unlabeled[0].occupant_text == "R/V Amber Tide"
    assert unlabeled[0].berth_length_ft is None
    assert unlabeled[0].day == date(1997, 8, 15)

    labeled = [e for e in entries if e.berth_code == "South Float East"]
    assert len(labeled) == 1


def test_true_blank_row_still_ends_the_month_block(tmp_path):
    rows = [
        ["AUGUST 1997"] + [""] * 31,
        [""] + [str(d) for d in range(1, 32)],
        ["South Float East - 90'", "", "", "S/V FAR HORIZON"] + [""] * 28,
        [""] * 32,  # genuine blank separator, no data at all
        ["SEPTEMBER 1997"] + [""] * 31,
        [""] + [str(d) for d in range(1, 31)] + [""],
        ["South Float East - 90'", "S/V FAR HORIZON"] + [""] * 30,
    ]
    path = write_csv(tmp_path, rows)
    entries = parse_grid_csv(path)
    months = sorted({e.day.month for e in entries})
    assert months == [8, 9]


def test_classify_occupant_vessel_vs_event():
    assert classify_occupant("S/V FAR HORIZON")[0] == "vessel"
    assert classify_occupant("Community sail day")[0] == "event"


def test_classify_occupant_maintenance_and_note_text():
    # Found while auditing real conflicts in the full 23-year import: these
    # were being filed as fake vessels before EVENT_KEYWORDS/NON_VESSEL_SHAPE_RE
    # were expanded to cover them.
    assert classify_occupant("Bollard replacement, west face")[0] == "event"
    assert classify_occupant("Fueling @0800")[0] == "event"
    assert classify_occupant("Rescue drill")[0] == "event"
    assert classify_occupant("1400")[0] == "note"
    assert classify_occupant("ETA 1200")[0] == "note"


def test_day_numbers_embedded_in_header_row(tmp_path):
    # Some years (2005+) put the day numbers on the month-header row itself
    # ("JANUARY 2005,1,2,3,...") with the weekday-letter row after it,
    # instead of the header being its own row. An earlier version of the
    # parser only ever looked *after* the header for the day-number row and
    # silently produced zero bookings for every month laid out this way.
    rows = [
        ["JANUARY 2005"] + [str(d) for d in range(1, 32)],
        [""] + ["S", "M", "T", "W", "TR", "F", "S"] * 4 + ["S", "M", "T"],
        ["North Pier West - 410'", "R/V CLEAR SEXTANT"] + [""] * 30,
        [""] * 32,
    ]
    path = write_csv(tmp_path, rows)
    entries = parse_grid_csv(path)
    assert len(entries) == 1
    assert entries[0].day == date(2005, 1, 1)
    assert entries[0].occupant_text == "R/V CLEAR SEXTANT"


def test_bare_month_name_header_uses_filename_year(tmp_path):
    # Some years (2014+) label the month header with just the bare month
    # name ("January") and no year at all -- the year has to come from
    # context (the source file/sheet, which is always scoped to one year).
    rows = [
        ["January", ""] + [str(d) for d in range(1, 32)],
        ["", ""] + ["W", "TR", "F"] + [""] * 28,
        ["North Pier West - 410'", "", "R/V GOLDEN COMPASS"] + [""] * 29,
        [""] * 33,
        ["February", ""] + [str(d) for d in range(1, 29)] + [""] * 3,
        ["", ""] + ["S", "S", "M"] + [""] * 28,
        ["North Pier West - 410'", "", "", "M/V NORTHERN HARBOR"] + [""] * 28,
    ]
    path = tmp_path / "dock_schedule_2014.csv"
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)

    entries = parse_grid_csv(path)
    assert {e.day.year for e in entries} == {2014}
    assert {e.day.month for e in entries} == {1, 2}
    # The bare "February" header must be recognized as a block boundary
    # inside the berth-row loop too, not just by the outer scan -- otherwise
    # it gets misparsed as a berth named "February".
    assert all(e.berth_code == "North Pier West" for e in entries)


def test_corrupted_day_row_that_does_not_start_at_one_is_rejected(tmp_path):
    # Real example from the source workbook's own 2010 tab: a header row
    # ("NOVEMBER 2018", mislabeled and containing vessel names shifted into
    # what should be pure day-number cells) had digit cells present but
    # starting at 7, not 1. Accepting it as the day-number row silently
    # produced bookings on the wrong days. Such a block should be skipped
    # entirely rather than guessed at.
    rows = [
        ["NOVEMBER 2018", "", "F/V GHOST", "", "", "", "", "7", "8", "9"],
        [""] * 10,
    ]
    path = tmp_path / "schedule.csv"
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    entries = parse_grid_csv(path)
    assert entries == []
