# WHOI Berth Reservation System

[![tests](https://github.com/sofiagn05/whoi-berth-system/actions/workflows/test.yml/badge.svg)](https://github.com/sofiagn05/whoi-berth-system/actions/workflows/test.yml)

A replacement for the spreadsheet grid used to track berth reservations by hand.
Given a berth (of a known length) and a date range, the system tells you
immediately whether a reservation is safe to make — no more eyeballing a
grid for overlaps or guessing whether a boat will actually fit.

See [SPEC.md](SPEC.md) for the problem statement, requirements and success
criteria, constraints, assumptions, and — for every piece of logic below —
exactly how it was validated, not just asserted to work.

**Data provenance.** The prompt for this project stated: *"A sample
schedule is attached to this email, containing 23 years of bookings."*
`data/dock_schedule_workbook.ods` is that attachment — the full
23-tab workbook (1997–2019), downloaded and provided directly rather than
reconstructed or mocked. `data/years/*.csv` are that same workbook's year
tabs, exported by `scripts/export_ods_years.py` (see **The real data**,
below); `data/vessel_dimensions_sample.csv` is the one file in `data/`
that isn't from that attachment — it's dimension data I put together
myself to demonstrate `POST /vessels/import`, documented as such in
**Closing the vessel-dimensions gap**.

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
make import   # sets up a venv and loads all 23 real years of the dock schedule (1997-2019)
make run      # serves the app at http://localhost:8420

# or, instead of `make import`, load small synthetic demo data with an
# intentional, easy-to-see conflict baked in -- useful as a quick sanity
# check of the resolver in isolation:
# make seed

make test     # runs the test suite (also runs automatically on every push, see badge above)
```

No `make`? The equivalent by hand:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.import_schedule data/years/dock_schedule_*.csv   # or: python -m app.seed
uvicorn app.main:app --reload --port 8420
```

That loads all 23 years (1997-2019) from `data/years/*.csv`, which were
exported from the source workbook (`data/dock_schedule_workbook.ods`) with
`scripts/export_ods_years.py` — see **The real data: what 23 years of it
actually looked like**, below, for what that took.

Once it's running, upload `data/vessel_dimensions_sample.csv` under "Vessel
dimensions" on the homepage (or `curl -F file=@data/vessel_dimensions_sample.csv
localhost:8420/vessels/import`) to activate fit-checking for the 15 vessels
it covers — see **Closing the vessel-dimensions gap**, below.

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
     resource. The weight is **priority first, reservation length
     second** (see *Priority-aware scheduling*, below): among
     reservations of equal priority, a 15-day research cruise is kept
     over a 1-day community sail day it conflicts with, the same way a
     plain "maximize total length" rule would; but a short, high-priority
     reservation is never displaced by a longer, lower-priority one, which
     a length-only rule would get backwards.
  2. **Greedily reassign whatever got displaced** to the best available,
     physically-fitting berth elsewhere, tracking a live occupancy
     timeline so two displaced bookings can't collide with each other
     either. A displaced vessel with unknown dimensions is not silently
     reassigned — it's placed but flagged `needs_review`, since fit can't
     be verified. Anything with no valid berth at all is reported as
     unresolved, not guessed at.

  The endpoint is a preview by default; nothing is written to the database
  until the plan is explicitly applied.

  Across all 23 real years, every double-booking that turns up lands on
  the `Unidentified` pseudo-berth (see below) — so there's a real
  conflict to resolve, but the "correct" fix (which of the two *actual*
  unlabeled berths each reservation belongs to) isn't something the data
  can answer. `python -m app.seed` instead gives a small, clean,
  intentional conflict on a normal named berth if you want to see the
  weight-preference behavior in isolation.

## Priority-aware scheduling

Not all of WHOI's work carries the same operational stakes. A funded,
time-boxed expedition under a standing research program is not
interchangeable with a routine guest visit when a berth conflict has to
be resolved one way or the other. Every booking now carries a priority
tier — `routine` (default), `elevated` (standing institutional programs),
or `critical` (time-boxed, mission-critical work) — set from the booking
form or the API.

This isn't cosmetic: it changes what the resolver actually decides.
`app/resolver.py::_booking_weight` ranks priority ahead of reservation
length, so a 2-day `critical` mission is protected over a 21-day
`routine` booking it conflicts with, not the other way around — verified
directly in `tests/test_resolver.py::test_critical_priority_wins_even_against_a_much_longer_booking`,
and end-to-end against real ORM objects (not just the pure scheduling
function) by inserting exactly that conflict into a live database and
confirming the resolver's output.

Priority isn't only set by hand, either. During import, vessels already
classified as research vessels or Coast Guard cutters (from the same R/V
and USCGC prefix parsing used to infer vessel type) default to `elevated`
rather than `routine` — 868 of the 2,258 bookings across all 23 years
qualify. This is a starting default that reflects WHOI's own standing
research operations, not a judgment on any individual booking, and any
reservation's priority can be adjusted afterward. The grid marks
`elevated` and `critical` bookings with a colored ring so the distinction
is visible at a glance, not just in the data.

## The real data: what 23 years of it actually looked like

The sample originally provided was one CSV export of a single tab (1997)
from a larger workbook. I later got the full source workbook
(`data/dock_schedule_workbook.ods`) and found it actually holds all 23
year tabs (1997-2019), plus a vessel-contact registry, a guest-yacht
registry, a pre-computed usage summary, and a "Tours" tab explicitly
marked stale ("Tours are now tracked in a separate workbook; this tab is
kept for reference") -- which I left untouched rather than importing.
`scripts/export_ods_years.py` pulls the 23 year tabs into the same raw
grid CSV format the importer already parsed: each month is a header row,
a day-of-month row, then one row per berth, with occupied cells naming
the vessel or event, and berth length embedded in the row label
(`North Pier West - 410'`).

What I didn't expect was that the layout isn't consistent *within that
one workbook* -- pointing the importer at all 23 years surfaced five
more real problems, on top of format assumptions that happened to hold
for the single 1997 CSV I started with:

- **The header/weekday/day-number row order has (at least) three
  different arrangements** across the 23 years -- header alone then a
  weekday row then a day-number row (1997); header with the weekday
  letters packed in, day-numbers separate (1997, other months); and
  header with the *day numbers* packed in, weekday row after (2005
  onward). A parser that assumes a fixed offset -- or even that day
  numbers are never on the header row -- gets the third case wrong.
  Fixed by scanning the header row and the next few rows for whichever
  one is mostly digits, regardless of position.
- **Some years drop the year entirely from the header** ("January"
  instead of "JANUARY 2014"). The year then has to come from context --
  I use the year embedded in each file's own name, since each source
  file already corresponds to exactly one year. Missing this meant 15 of
  the 23 years imported zero bookings on the first attempt.
- **Not every berth row uses the `NAME - NNN'` format.** "North Finger
  Piers:" (colon, no length) and "Small craft slips (institution boats)"
  (no length at all) are both real, frequently-booked berths that a
  strict length-pattern match silently dropped -- six years of "North
  Finger Piers:" reservations, gone, before this was caught. They're now
  kept under their own names with an unknown length, the same
  unknown-is-explicit treatment used for missing vessel dimensions.
- **One row has no berth label at all.** A blank-label row with real
  occupant data used to be indistinguishable from a genuinely blank
  separator row ending the month's block -- silently dropping the
  reservation, which is worse than a misplaced one. It's now kept under
  an explicit `Unidentified (unlabeled row in source schedule)`
  pseudo-berth instead (134 bookings across all 23 years), excluded from
  `/bookings/suggest` and the resolver as a *destination* since fit can't
  be verified for a berth of unknown length.
- **A header row was itself corrupted in the source data.** The 2010
  tab's November and December blocks are labeled "NOVEMBER 2018" /
  "DECEMBER 2018" (a real typo, thirteen years off) and have vessel names
  spilled into what should be pure day-number cells. Accepting that
  row as the day-number row produced bookings dated in 2018 and a
  garbage "vessel" literally named `1400`. Fixed with a sanity check: a
  genuine day-number row always starts counting at 1; a row with several
  digit cells that doesn't is rejected, and that month's block is skipped
  rather than importing data built on a guess.
- **Occupant cells sometimes hold a scheduling note, not a name** --
  a bare time (`1400`), an `ETA 1200` remark, or an operational item like
  `Bollard replacement, west face` or `Fueling @0800`. These aren't
  reservations and are now recognized and dropped (`notes_skipped` in the
  import stats) or classified as non-vessel events, rather than becoming
  fake vessels in the registry.

**With all of that fixed, does 23 years of data actually contain the
double-bookings the original problem describes?** Yes, but with an
important caveat: all 9 conflicts found land on the `Unidentified`
pseudo-berth. That's expected, not a coincidence -- it's a catch-all for
every reservation whose *true* berth was never recorded, so two
same-day entries landing there means two things happened on the same day
somewhere unlabeled, not necessarily the same physical spot. Every
correctly-labeled, real-named berth is conflict-free across all 23
years. Read that either way you like: as evidence the manual process
mostly worked when a berth was actually written down, or as the reason
that grid needed a computer in the first place -- unlabeled reservations
are exactly the ones nobody could visually check for a conflict either.

## Closing the vessel-dimensions gap

Since the schedule doesn't carry LOA/beam/draft, `POST /vessels/import`
takes a CSV (`name, loa_ft, beam_ft, draft_ft, vessel_type`) and fills in
dimensions for vessels that already exist, matching by name — it updates,
never creates, so a typo'd name in the CSV surfaces as "not found" instead
of silently spawning a duplicate vessel. `data/vessel_dimensions_sample.csv`
covers the 15 vessels from the original 1997 sample; across all 23 years
there are 484 distinct vessels and 2,258 bookings total, so that CSV only
closes a small slice of the full gap — uploading it activates fit-checking
for those 15, the rest stay in the honest "unknown" state until their
dimensions are filled in too.

That's not a made-up number to fill in, either: the source workbook's
"Science" and "Yachts" tabs are a real vessel/contact registry, with LOA
(and, for guest yachts, draft) recorded per vessel — e.g. `M/Y Western
Strand 52'` alongside an explicit `LOA: 65', Draft: 4'` field (the two
numbers disagree, which is its own small data-quality note: the name's
suffix looks like a model designation, not a reliable length, so the
explicit field should win). I didn't parse these tabs into the vessel
registry — they're a genuinely messy, multi-row-per-vessel layout — but
extending `POST /vessels/import`'s CSV path to cover their fields is a
scoped, well-understood next step, not a design question.

## Searching the history

`GET /bookings/search?q=<text>` finds bookings by vessel or event name
(substring, case-insensitive) across every year loaded, and returns which
berth and month each match is in. Browsing 23 years of history one month at
a time doesn't scale for "when did this vessel last berth here" — the UI's
search box jumps straight to the matching month in the calendar grid.

## Interface

The live app (`static/index.html`/`app.js`/`style.css`) uses a dark,
high-density interface built for someone who'll be looking at this
schedule daily, not a marketing page: a floating action button opens
booking creation as a modal instead of taking over the page, the
vessel/event choice is a segmented pill control (not a `<select>`
dropdown) wired to the same underlying form logic, and three key-stat
cards up top (occupancy rate, total conflicts, active berths) are
computed live from `/analytics` and `/audit` rather than hardcoded. A
double-booked grid cell gets a diagonal-stripe overlay and a pulsing
alert glow so it reads as urgent at a glance, not just a different
background color; elevated/critical-priority bookings get their own
ring color, distinct from the conflict state. All of this sits on top
of the same endpoints and IDs the rest of this README describes — the
redesign changed how the interface looks and is driven, not what it
calls or how the booking/audit/resolver logic behaves, which is why the
existing screenshots and behavior described elsewhere in this doc still
hold.

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

- **The Science/Yachts vessel registry isn't parsed into the database.**
  Real LOA/draft data exists for a meaningful chunk of the 484 vessels;
  extending the CSV importer to read those tabs (instead of a hand-built
  CSV) would close most of the remaining fit-checking gap. Scoped above.
- **A pre-computed "8YR Dock Summary" tab lists two berths** ("North
  Finger Piers" -- now imported -- and "Marsh Landing") **that never
  appear as a labeled row in any year's actual grid.** "Marsh Landing"
  in particular doesn't show up anywhere in the 23 years of grid data at
  all, only in that summary — meaning it's either an even older label
  format this importer hasn't seen, or berth data that predates 1997.
  Not investigated further; flagging it rather than guessing.
- **~61 rows across 23 years (about 2% of all entries) sit in the gap
  between two month blocks** -- a blank-labeled row with real occupant
  data, positioned after the blank separator that ends one month but
  before the next month's header is recognized. Unlike the in-block
  unlabeled-row case, there's no reliable way to tell whether these
  belong to the end of the prior month or the start of the next one
  without guessing at a date, so they're currently dropped rather than
  assigned a possibly-wrong one. Counting and surfacing them (the way
  `unlabeled_berth_bookings` already does) instead of silently dropping
  them would be the honest next step, even without a confident fix.

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
  attributes none of the 23 years of schedule data carries (draft,
  guest/novice status), so they're currently dead weight against real
  data specifically -- they were named directly in the brief, so I kept
  them rather than cut them, but they only start doing anything once
  that data is entered for real vessels (the Science/Yachts registry
  above is where draft, at least, would come from). Worth knowing rather
  than assuming they're already active.
- **Two additions were about the *process*, not the app**: a `Makefile`
  (`make import && make run` instead of four manual commands) so trying
  this out has no setup friction, and a GitHub Actions workflow that runs
  the test suite on every push, so "tests pass" isn't something that only
  happens when someone remembers to run `pytest` locally (see the badge at
  the top of this file).
