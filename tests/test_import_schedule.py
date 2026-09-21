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
