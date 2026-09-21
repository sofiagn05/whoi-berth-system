# WHOI Berth Reservation System

[![tests](https://github.com/sofiagn05/whoi-berth-system/actions/workflows/test.yml/badge.svg)](https://github.com/sofiagn05/whoi-berth-system/actions/workflows/test.yml)

A replacement for the spreadsheet grid used to track berth reservations by hand.
Given a berth (of a known length) and a date range, the system tells you
immediately whether a reservation is safe to make — no more eyeballing a
grid for overlaps or guessing whether a boat will actually fit.

## Why this matters to me

WHOI's Schiller Center for Reef Solutions is racing to find and protect
"super corals" before warming waters wipe them out, and the Fowler Center
is building the tracking infrastructure to prove out ocean-based carbon
removal — both real deadlines against a collapsing timeline. A dock
schedule that needs someone to squint at a spreadsheet to catch a
double-booking is exactly the kind of overhead that shouldn't be
competing for anyone's attention when the actual work is this urgent.
This project isn't just "build a scheduler" — it's removing one small,
boring failure point so the people doing the reef and climate work don't
have to think about berths at all.

## Quickstart

```bash
make import   # sets up a venv and loads the real 1997 dock schedule sample
make run      # serves the app at http://localhost:8420

# or, instead of `make import`, load synthetic demo data with intentional
# conflicts baked in -- useful for seeing the resolver actually do something,
# since the real 1997 sample happens to be conflict-free:
# make seed

make test     # runs the test suite (also runs automatically on every push, see badge above)
```

No `make`? The equivalent by hand:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.import_schedule data/dock_schedule_1997.csv   # or: python -m app.seed
uvicorn app.main:app --reload --port 8420
```

Once it's running, upload `data/vessel_dimensions_sample.csv` under "Vessel
dimensions" on the homepage (or `curl -F file=@data/vessel_dimensions_sample.csv
localhost:8420/vessels/import`) to see fit-checking go from "unknown" to
active for the real 1997 vessels — see **Closing the vessel-dimensions gap**,
below.

## What it actually checks

Two things, both named directly in the original problem:

1. **Double-booking.** Every new reservation is checked against every
   existing reservation on the same berth for date overlap
   (`app/conflicts.py::date_ranges_overlap`). This runs both at write time
   (a booking that would conflict is rejected with a 409 and the specific
   conflicting reservation named) and retroactively (`GET /audit` scans the
   whole history for existing conflicts, since historical data can already
   contain them).

2. **Physical fit.** A reservation is checked against the berth's length,
   and — where known — beam and draft. Length uses a hard fail (can't
   physically fit) plus a soft warning if the berth gives less than the
   industry-standard 10% length margin over the vessel's LOA. Beam/draft
   are hard fails when the berth has a recorded limit. If a vessel's
   dimensions aren't on file, the system says so explicitly rather than
   silently skipping the check or guessing.

A third feature that isn't strictly "checking," but follows from having the
first two: **berth suggestion** (`GET /bookings/suggest`). Given a vessel and
a date range, it returns berths that are free and fit, ranked to prefer an
efficient (not wastefully oversized) berth, nudged toward channel-adjacent
berths for deep-draft vessels and toward T-head berths for guest/novice
crews — the maneuverability and tidal considerations from the brief,
expressed as a ranking heuristic rather than a hard rule.

The audit endpoint also uses this to propose a fix for every conflict it
finds, not just flag it.

## Beyond checking: a dashboard and a global conflict resolver

Two features go past "detect and flag" into "understand the pattern" and
"fix everything at once":

- **Dashboard** (`GET /analytics`, rendered on the homepage). Berth
  utilization over the full booking history, double-booked days per month,
  and the busiest vessels by days berthed. This is the question an audit
  list can't answer on its own: not just *is there a conflict*, but *which
  berths are actually under pressure*.

- **Batch conflict resolution** (`GET /audit/resolve`, `POST
  /audit/resolve/apply`). The single-booking suggestion endpoint answers
  "where could this one reservation go." This answers a harder question:
  given *every* conflict in the system at once, what's the best global
  reassignment? It's a two-stage algorithm, not a loop over individual
  fixes:

  1. **Per berth, decide which of its (possibly mutually overlapping)
     reservations to keep in place**, using weighted interval scheduling
     (`app/resolver.py::weighted_interval_schedule_keep`) — the classic
     Kleinberg–Tardos DP, which is *provably optimal* for a single
     resource. Weight = reservation length in days, so a 15-day research
     cruise is kept over a 1-day community sail day it conflicts with;
     plain "maximize the count of bookings kept" would do the opposite,
     since dropping the long booking frees more slots for short ones.
  2. **Greedily reassign whatever got displaced** to the best available,
     physically-fitting berth elsewhere, tracking a live occupancy
     timeline so two displaced bookings can't collide with each other
     either. A displaced vessel with unknown dimensions is not silently
     reassigned — it's placed but flagged `needs_review`, since fit can't
     be verified. Anything with no valid berth at all is reported as
     unresolved, not guessed at.

  The endpoint is a preview by default; nothing is written to the database
  until the plan is explicitly applied.

  The 1997 sample data happens to be conflict-free, so there's nothing for
  the resolver to visibly do against it — run `python -m app.seed` instead
  (on a fresh `berths.db`) to see it resolve an actual double-booking
  end-to-end, including the weight-preference behavior described above.

## A note on the real data

The attached sample (`data/dock_schedule_1997.csv`) is the literal grid:
each month is a header row, a day-of-month row, then one row per berth,
with occupied cells naming the vessel or event. Berth length is embedded
in the row label (`North Pier West - 410'`), which the importer parses
directly rather than requiring it to be re-entered.

Two things worth flagging about this data:

- **The row layout isn't consistent between months.** In August, the
  weekday abbreviations (M/T/W/...) sit on their own row between the month
  header and the day numbers. In September onward, they're packed into the
  header row itself. A parser that assumes a fixed row offset silently
  drops every month after the first — it did, in an earlier version of
  this importer. The fix (`app/import_schedule.py::parse_grid_csv`) finds
  the day-number row by content (first blank-label row that's mostly
  digits) instead of by position. Worth knowing before pointing this at
  the other 22 years of tabs, which may have their own quirks.
- **Vessel dimensions (LOA/beam/draft) are not in this schedule at all** —
  only names and berth assignments. The importer creates vessel records
  from the schedule with dimensions left unset, and the fit-checker treats
  "unknown" as its own explicit state (a warning, not a silent pass).
  Fit-checking becomes fully active for a vessel as soon as its dimensions
  are filled in — see **Closing the vessel-dimensions gap**, below.
- **One row has no berth label at all.** Below "South Float East" in
  August, a row holding "R/V Amber Tide" has a blank first column — a
  berth whose name and length were simply left blank in the source
  spreadsheet. The first version of this importer treated any blank-label
  row as the end of the month's block and silently dropped it — which is
  worse than the month-layout bug above, since it loses a real
  reservation rather than just misplacing one. It's now kept under an
  explicit `Unidentified (unlabeled row in source schedule)` pseudo-berth
  with an unknown length (`app/import_schedule.py` flags this with a
  count on every import run), rather than invented or discarded. It shows
  up in the calendar and audit like any other berth, but is excluded from
  `/bookings/suggest` and the batch resolver as a *destination*, since
  fit can't be verified for a berth of unknown length — the same
  unknown-is-explicit treatment already used for missing vessel
  dimensions, applied to the berth side too.

## Closing the vessel-dimensions gap

Since the schedule doesn't carry LOA/beam/draft, `POST /vessels/import`
takes a CSV (`name, loa_ft, beam_ft, draft_ft, vessel_type`) and fills in
dimensions for vessels that already exist, matching by name — it updates,
never creates, so a typo'd name in the CSV surfaces as "not found" instead
of silently spawning a duplicate vessel. `data/vessel_dimensions_sample.csv`
has plausible dimensions for all 15 vessels in the 1997 sample. Uploading it
(via the UI or `curl -F file=@data/vessel_dimensions_sample.csv
localhost:8420/vessels/import`) drops the audit's fit warnings from 31 to 1
— the one that's left is the Amber Tide booking on the unlabeled berth
above, correctly still unverifiable since it's the *berth's* length that's
missing this time, not the vessel's.

## Searching the history

`GET /bookings/search?q=<text>` finds bookings by vessel or event name
(substring, case-insensitive) across every year loaded, and returns which
berth and month each match is in. Browsing 23 years of history one month at
a time doesn't scale for "when did this vessel last berth here" — the UI's
search box jumps straight to the matching month in the calendar grid.

## Data model

- **Berth** — code, length, optional max beam/draft, a location category
  (T-head / alongside / inner / channel-adjacent) used by the suggestion
  heuristic.
- **Vessel** — name, optional LOA/beam/draft, type, guest/novice flags.
- **Booking** — either a vessel reservation or a non-vessel event (e.g. a
  community sail day) on a berth for a date range. Events and vessels share
  the same table because they compete for the same resource (the berth) —
  the whole point is that a sail day and a boat can't both be assigned the
  same berth on the same day.

## What's not built

Importing all 23 years and cross-referencing a real vessel registry for
dimensions would need the rest of the workbook's tabs — the importer is
written to take a list of yearly CSV exports (`python -m app.import_schedule
year1.csv year2.csv ...`) and is idempotent (safe to re-run), so that's a
data problem, not a code problem, once the rest of the tabs are exported.

## A waste pass, after the features landed

Once the checking, dashboard, and resolver were working, I went back through
looking for what shouldn't have been there in the first place, what could be
deleted, and what was duplicated — before adding any more polish:

- **Two unused imports removed** (`sqlalchemy.and_` in `crud.py`, `SessionLocal`
  in `main.py`) — left over from earlier drafts, never actually called.
- **The same `check_fit(...)` six-argument call was duplicated at four call
  sites** (`crud.py` x2, `main.py`, `resolver.py`), each manually unpacking
  `berth.length_ft`, `vessel.loa_ft`, etc. Collapsed into one adapter,
  `crud.fit_issues(berth, vessel)`, used everywhere fit needs checking.
- **Day-by-day date iteration was duplicated** between the calendar endpoint
  and the analytics module. Both now share `conflicts.iter_days(start, end)`.
- **Two heuristics can't currently fire against the real data**: the
  channel-adjacent nudge for deep-draft vessels and the T-head nudge for
  guest/novice crews (`crud.score_berth_for_vessel`) both depend on vessel
  attributes the 1997 schedule doesn't carry (draft, guest/novice status),
  so they're currently dead weight against real data specifically -- they
  were named directly in the brief, so I kept them rather than cut them,
  but they only start doing anything once that data is entered for real
  vessels. Worth knowing rather than assuming they're already active.
- **Two additions were about the *process*, not the app**: a `Makefile`
  (`make import && make run` instead of four manual commands) so trying
  this out has no setup friction, and a GitHub Actions workflow that runs
  the test suite on every push, so "tests pass" isn't something that only
  happens when someone remembers to run `pytest` locally (see the badge at
  the top of this file).
