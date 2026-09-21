"""Import the legacy grid-style dock schedule (one CSV export per year/tab)
into the normalized berths.db database.

The source format is the literal grid staff used to check by eye: each
month is a block of rows, headed by a "MONTH YEAR" label, followed by a
weekday-abbreviation row and a day-of-month row, followed by one row per
berth. A berth row's label embeds its length, e.g. "North Pier West - 410'".
Each filled cell names the occupying vessel (e.g. "S/V FAR HORIZON") or a
non-vessel event (e.g. "Community sail day").

Vessel dimensions (LOA/beam/draft) are NOT present in this schedule, so
imported vessels are created with unknown dimensions. Fit-checking for a
given vessel becomes active once someone fills in its dimensions via the
API/UI or the Vessel table directly.

Usage:
    python -m app.import_schedule path/to/export1.csv path/to/export2.csv ...
"""

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from app.database import Base, SessionLocal, engine
from app.models import Berth, Booking, BookingKind, Vessel

MONTHS = {
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4, "MAY": 5, "JUNE": 6,
    "JULY": 7, "AUGUST": 8, "SEPTEMBER": 9, "OCTOBER": 10, "NOVEMBER": 11, "DECEMBER": 12,
}
MONTH_HEADER_RE = re.compile(r"^([A-Z]+)\s+(\d{4})$")
BERTH_LABEL_RE = re.compile(r"^(.+?)\s*-\s*(\d+)\s*'?\s*$")
VESSEL_PREFIX_RE = re.compile(r"^(S/V|F/V|M/V|R/V|USCGC|Tug|Barge)\s+(.+)$", re.IGNORECASE)
EVENT_KEYWORDS = [
    "sail day", "community", "open house", "regatta", "festival",
    "cleanup", "clean-up", "launch ceremony", "memorial", "tour", "race",
]
PREFIX_TYPES = {
    "s/v": "sailing vessel", "f/v": "fishing vessel", "m/v": "motor vessel",
    "r/v": "research vessel", "uscgc": "coast guard cutter", "tug": "tug", "barge": "barge",
}


@dataclass
class RawEntry:
    berth_code: str
    berth_length_ft: int
    day: date
    occupant_text: str


def classify_occupant(text: str):
    """Returns (kind, name, vessel_type) for a raw occupant cell string."""
    text = text.strip()
    m = VESSEL_PREFIX_RE.match(text)
    if m:
        prefix, name = m.groups()
        return "vessel", name.strip(), PREFIX_TYPES.get(prefix.lower(), "unknown")
    lowered = text.lower()
    if any(k in lowered for k in EVENT_KEYWORDS):
        return "event", text, None
    return "vessel", text, "unknown"


def parse_grid_csv(path: Path) -> list[RawEntry]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    entries: list[RawEntry] = []
    i, n = 0, len(rows)
    while i < n:
        label = (rows[i][0] if rows[i] else "").strip()
        m = MONTH_HEADER_RE.match(label)
        if not m:
            i += 1
            continue

        month = MONTHS.get(m.group(1))
        year = int(m.group(2))

        # The row layout between the month header and the first berth row is
        # inconsistent across blocks: some months put weekday abbreviations
        # (M, T, W, ...) on their own row after the header, others pack them
        # into the header row itself. Rather than assume a fixed offset, scan
        # forward for the first blank-label row whose cells are mostly digits
        # -- that is unambiguously the day-of-month row.
        j = i + 1
        day_row_idx = None
        while j < n:
            row = rows[j]
            if (row[0] if row else "").strip():
                break
            digit_count = sum(1 for c in row[1:] if c.strip().isdigit())
            if digit_count >= 5:
                day_row_idx = j
                break
            j += 1

        if month is None or day_row_idx is None:
            i = j + 1
            continue

        day_numbers = [int(c) if c.strip().isdigit() else None for c in rows[day_row_idx][1:]]
        i = day_row_idx + 1

        while i < n:
            row = rows[i]
            label = (row[0] if row else "").strip()
            if not label:
                i += 1
                break
            if MONTH_HEADER_RE.match(label):
                break
            bm = BERTH_LABEL_RE.match(label)
            if not bm:
                i += 1
                continue
            berth_code, berth_len = bm.group(1).strip(), int(bm.group(2))
            for col_idx, cell in enumerate(row[1:]):
                cell = cell.strip()
                if not cell or col_idx >= len(day_numbers) or day_numbers[col_idx] is None:
                    continue
                try:
                    d = date(year, month, day_numbers[col_idx])
                except ValueError:
                    continue
                entries.append(RawEntry(berth_code, berth_len, d, cell))
            i += 1

    return entries


def coalesce_bookings(entries: list[RawEntry]):
    """Merge runs of consecutive calendar days on the same berth with the
    same occupant text into a single (start, end) range, in case a future
    year's data contains genuine multi-day stays (unlike the 1997 sample,
    where every occupied cell was a single day)."""
    by_berth: dict[str, list[RawEntry]] = {}
    for e in entries:
        by_berth.setdefault(e.berth_code, []).append(e)

    bookings = []
    for berth_code, elist in by_berth.items():
        elist.sort(key=lambda e: e.day)
        run_start = run_end = None
        run_text = None
        run_len = None
        for e in elist:
            if run_start is not None and e.occupant_text == run_text and e.day == run_end + timedelta(days=1):
                run_end = e.day
                continue
            if run_start is not None:
                bookings.append((berth_code, run_len, run_start, run_end, run_text))
            run_start, run_end, run_text, run_len = e.day, e.day, e.occupant_text, e.berth_length_ft
        if run_start is not None:
            bookings.append((berth_code, run_len, run_start, run_end, run_text))
    return bookings


def import_files(paths: list[Path], db=None):
    owns_session = db is None
    if owns_session:
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()

    berth_cache = {b.code: b for b in db.query(Berth).all()}
    vessel_cache = {v.name.lower(): v for v in db.query(Vessel).all()}
    existing_bookings = {
        (b.berth_id, b.vessel_id, b.event_name, b.start_date, b.end_date) for b in db.query(Booking).all()
    }

    stats = {"files": 0, "berths_created": 0, "vessels_created": 0, "bookings_created": 0, "bookings_skipped_dupe": 0}

    for path in paths:
        stats["files"] += 1
        entries = parse_grid_csv(path)
        for berth_code, berth_len, start, end, text in coalesce_bookings(entries):
            berth = berth_cache.get(berth_code)
            if berth is None:
                berth = Berth(code=berth_code, length_ft=berth_len)
                db.add(berth)
                db.flush()
                berth_cache[berth_code] = berth
                stats["berths_created"] += 1

            kind, name, vessel_type = classify_occupant(text)

            if kind == "event":
                key = (berth.id, None, name, start, end)
                if key in existing_bookings:
                    stats["bookings_skipped_dupe"] += 1
                    continue
                db.add(Booking(kind=BookingKind.EVENT, berth_id=berth.id, event_name=name,
                                start_date=start, end_date=end))
                existing_bookings.add(key)
                stats["bookings_created"] += 1
            else:
                vessel = vessel_cache.get(name.lower())
                if vessel is None:
                    vessel = Vessel(name=name, vessel_type=vessel_type)
                    db.add(vessel)
                    db.flush()
                    vessel_cache[name.lower()] = vessel
                    stats["vessels_created"] += 1
                key = (berth.id, vessel.id, None, start, end)
                if key in existing_bookings:
                    stats["bookings_skipped_dupe"] += 1
                    continue
                db.add(Booking(kind=BookingKind.VESSEL, berth_id=berth.id, vessel_id=vessel.id,
                                start_date=start, end_date=end))
                existing_bookings.add(key)
                stats["bookings_created"] += 1

    db.commit()
    if owns_session:
        db.close()
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_files", nargs="+", type=Path)
    args = parser.parse_args()

    missing = [p for p in args.csv_files if not p.exists()]
    if missing:
        print(f"File(s) not found: {missing}", file=sys.stderr)
        sys.exit(1)

    stats = import_files(args.csv_files)
    print(stats)
