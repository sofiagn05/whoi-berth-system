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
from typing import Optional

from app.database import Base, SessionLocal, engine
from app.models import Berth, Booking, BookingKind, Vessel

MONTHS = {
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4, "MAY": 5, "JUNE": 6,
    "JULY": 7, "AUGUST": 8, "SEPTEMBER": 9, "OCTOBER": 10, "NOVEMBER": 11, "DECEMBER": 12,
}
UNLABELED_BERTH_CODE = "Unidentified (unlabeled row in source schedule)"
MONTH_HEADER_RE = re.compile(r"^([A-Z]+)\s+(\d{4})$")
BARE_MONTH_RE = re.compile(r"^(" + "|".join(m.capitalize() for m in MONTHS) + r")$")
FILENAME_YEAR_RE = re.compile(r"(\d{4})")
BERTH_LABEL_RE = re.compile(r"^(.+?)\s*-\s*(\d+)\s*'?\s*$")
VESSEL_PREFIX_RE = re.compile(r"^(S/V|F/V|M/V|R/V|USCGC|Tug|Barge)\s+(.+)$", re.IGNORECASE)
EVENT_KEYWORDS = [
    "sail day", "community", "open house", "regatta", "festival",
    "cleanup", "clean-up", "launch ceremony", "memorial", "tour", "race",
    # Operational/maintenance/training events -- discovered while auditing
    # real conflicts in the full 23-year import: without these, phrases
    # like "Bollard replacement, west face" or "Fueling @0800" were being
    # filed as fake vessels named after the maintenance task.
    "training", "drill", "provisioning", "fueling", "fuelling", "repair",
    "rebuild", "replacement", "maintenance", "inspection", "no docking",
    "no usage", "touch and go", "safety", "event", "construction", "paving",
    "concrete", "utility work", "film crew", "work near", "work on",
]
# A second, weaker signal: real vessel names in this dataset are short
# (1-4 words) proper nouns. Longer occupant text, or text containing a
# comma (e.g. "Bollard replacement, west face"), reads as a descriptive
# note rather than a name -- treated as an event too.
NON_VESSEL_SHAPE_RE = re.compile(r",")
# Scheduling annotations sometimes get written into an occupant cell
# alongside (or instead of) an actual vessel name -- e.g. an arrival-time
# note squeezed into the same grid as the boats. These are not
# reservations and would otherwise show up as fake "vessels" named "1400"
# or "11".
NOTE_RE = re.compile(r"^\d{1,4}$|^(eta|etd)\b", re.IGNORECASE)
PREFIX_TYPES = {
    "s/v": "sailing vessel", "f/v": "fishing vessel", "m/v": "motor vessel",
    "r/v": "research vessel", "uscgc": "coast guard cutter", "tug": "tug", "barge": "barge",
}


@dataclass
class RawEntry:
    berth_code: str
    berth_length_ft: Optional[int]
    day: date
    occupant_text: str


def classify_occupant(text: str):
    """Returns (kind, name, vessel_type) for a raw occupant cell string.
    kind is "vessel", "event", or "note" (a scheduling annotation that
    isn't a reservation at all, e.g. a bare time or an "ETA ..." remark)."""
    text = text.strip()
    if NOTE_RE.match(text):
        return "note", text, None
    m = VESSEL_PREFIX_RE.match(text)
    if m:
        prefix, name = m.groups()
        return "vessel", name.strip(), PREFIX_TYPES.get(prefix.lower(), "unknown")
    lowered = text.lower()
    if any(k in lowered for k in EVENT_KEYWORDS) or NON_VESSEL_SHAPE_RE.search(text):
        return "event", text, None
    return "vessel", text, "unknown"


def parse_grid_csv(path: Path, default_year: Optional[int] = None) -> list[RawEntry]:
    """default_year is used when a month header has no year of its own (some
    years format the header as a bare month name, e.g. "January" instead of
    "JANUARY 2014" -- the year then has to come from context). If not given
    explicitly, it's inferred from a 4-digit run in the filename, since each
    source file already corresponds to exactly one year.
    """
    if default_year is None:
        year_match = FILENAME_YEAR_RE.search(path.stem)
        default_year = int(year_match.group(1)) if year_match else None

    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    entries: list[RawEntry] = []
    i, n = 0, len(rows)
    while i < n:
        label = (rows[i][0] if rows[i] else "").strip()
        m = MONTH_HEADER_RE.match(label)
        bare_m = None if m else BARE_MONTH_RE.match(label)
        if not m and not bare_m:
            i += 1
            continue

        if m:
            month = MONTHS.get(m.group(1))
            year = int(m.group(2))
        else:
            month = MONTHS.get(bare_m.group(1).upper())
            year = default_year

        # The row layout between the month header and the first berth row
        # varies across the 23 years of this workbook -- at least three
        # different arrangements of the month header, the weekday-letter
        # row (M, T, W, ...), and the day-number row show up:
        #   - header alone; weekday row; day-number row (e.g. Aug 1997)
        #   - header+weekday combined; day-number row (e.g. Sep 1997)
        #   - header+day-numbers combined; weekday row after (e.g. 2005+)
        # Rather than assume a fixed offset -- or even that the day numbers
        # are never on the header row itself -- scan the header row and the
        # few rows after it for the first one whose cells are mostly
        # digits. An earlier version only scanned *after* the header, which
        # silently produced zero bookings for every year using the third
        # layout (2005 through at least 2019: 15 of the 23 years).
        day_row_idx = None
        for cand in range(i, min(i + 4, n)):
            row = rows[cand]
            cand_label = (row[0] if row else "").strip()
            if cand != i and cand_label:
                break  # ran into real content (a berth row) without finding day numbers
            candidate_days = [int(c) for c in row[1:] if c.strip().isdigit()]
            if len(candidate_days) < 5:
                continue
            # A genuine day-number row always starts counting at 1. A row
            # that happens to contain several digit cells for some other
            # reason (e.g. a header row corrupted by cells shifted in from
            # elsewhere in the source spreadsheet, seen in the source's own
            # 2010 tab) will not -- reject it rather than silently building
            # an offset day-to-column mapping that misdates every booking
            # in the block.
            if candidate_days[0] != 1:
                continue
            day_row_idx = cand
            break

        if month is None or day_row_idx is None:
            i += 1
            continue

        day_numbers = [int(c) if c.strip().isdigit() else None for c in rows[day_row_idx][1:]]
        i = day_row_idx + 1
        # A weekday-abbreviation row (blank label, short non-digit cells)
        # may sit immediately after the day-number row instead of before
        # it; skip it too so berth rows are detected starting right after.
        if i < n:
            row = rows[i]
            row_label = (row[0] if row else "").strip()
            digit_count = sum(1 for c in row[1:] if c.strip().isdigit())
            if not row_label and digit_count < 5 and any(c.strip() for c in row[1:]):
                i += 1

        while i < n:
            row = rows[i]
            label = (row[0] if row else "").strip()
            data_cells = row[1:] if row else []
            has_data = any(c.strip() for c in data_cells)

            if not label and not has_data:
                i += 1
                break  # genuinely blank separator row -- end of this month's block

            if MONTH_HEADER_RE.match(label) or BARE_MONTH_RE.match(label):
                break  # next month header (either format); don't consume it here

            if not has_data:
                # A label-only row with nothing booked -- either a genuinely
                # empty berth that month, or a stray artifact row (e.g. a
                # bare "January" label some years carry alongside the real
                # header). Either way there's nothing to extract; move on
                # without treating it as a berth or ending the block.
                i += 1
                continue

            if label:
                bm = BERTH_LABEL_RE.match(label)
                if bm:
                    berth_code, berth_len = bm.group(1).strip(), int(bm.group(2))
                else:
                    # A real berth row whose label doesn't encode a
                    # "- NNN'" length -- e.g. "North Finger Piers:" or
                    # "Small craft slips (institution boats)". Kept under
                    # its own name with an unknown length rather than
                    # silently dropping every reservation on it, which an
                    # earlier version did for six years of "North Finger
                    # Piers:" bookings before this fix.
                    berth_code, berth_len = label.rstrip(":").strip(), None
            else:
                # A row with reservation data but no berth label at all: a
                # gap in the source spreadsheet itself (a berth whose name
                # and length were left blank), not a parsing edge case.
                # Surfacing it as its own explicitly-unidentified berth beats
                # silently discarding a real historical reservation.
                berth_code, berth_len = UNLABELED_BERTH_CODE, None

            for col_idx, cell in enumerate(data_cells):
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

    stats = {
        "files": 0, "berths_created": 0, "vessels_created": 0, "bookings_created": 0,
        "bookings_skipped_dupe": 0, "unlabeled_berth_bookings": 0, "notes_skipped": 0,
    }

    for path in paths:
        stats["files"] += 1
        entries = parse_grid_csv(path)
        for berth_code, berth_len, start, end, text in coalesce_bookings(entries):
            kind, name, vessel_type = classify_occupant(text)
            if kind == "note":
                # A scheduling annotation (a bare time, an "ETA ..." remark)
                # that ended up in an occupant cell -- not a reservation,
                # so it's counted and dropped rather than imported as a
                # fake vessel or event.
                stats["notes_skipped"] += 1
                continue

            if berth_code == UNLABELED_BERTH_CODE:
                stats["unlabeled_berth_bookings"] += 1
            berth = berth_cache.get(berth_code)
            if berth is None:
                berth = Berth(code=berth_code, length_ft=berth_len)
                db.add(berth)
                db.flush()
                berth_cache[berth_code] = berth
                stats["berths_created"] += 1

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
    if stats["unlabeled_berth_bookings"]:
        print(
            f"\nNote: {stats['unlabeled_berth_bookings']} booking(s) came from a row in the "
            f"source spreadsheet with no berth name/length filled in. These were kept under "
            f"'{UNLABELED_BERTH_CODE}' rather than dropped -- reconcile them against the "
            f"original schedule and reassign to the correct berth via the API/UI.",
            file=sys.stderr,
        )
