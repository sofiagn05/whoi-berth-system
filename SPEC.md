# Problem definition, requirements, constraints, and validation

This is the requirements-engineering pass behind the system in
[README.md](README.md) — what the problem actually was, how "done" was
defined, what boundaries the design had to respect, what was assumed where
the source material was silent or ambiguous, and how each piece of logic
was actually checked rather than just written.

## 1. Problem statement

WHOI's marine research facility manages berths of varying lengths.
Vessels reserve a berth for specific ranges of days; non-vessel events
(e.g. community sail days) also occupy a berth for a range of days. The
schedule has historically been kept as a spreadsheet grid — berths as
rows, days as columns — checked by eye. Two failure modes follow directly
from that process:

1. **Double-bookings go undetected** until someone happens to notice two
   names in the same berth's row for overlapping days.
2. **Physical fit goes unverified** — nothing stops a vessel from being
   assigned to a berth shorter than its own length, or too narrow/shallow
   for its beam/draft.

The system's job is to make both of those checks automatic, for new
reservations and for whatever the 23 years of existing history already
contains, without requiring the underlying data to already be clean.

## 2. Requirements and success criteria

Pulled directly from the problem statement, each with how it was actually
verified (not just asserted):

| # | Requirement | Success criteria | How it was verified |
|---|---|---|---|
| R1 | Represent berths of varying lengths | A berth has a length; reservations can be checked against it | `models.Berth.length_ft`; `tests/test_conflicts.py` |
| R2 | Represent vessel reservations for date ranges | A vessel booking has a start/end date and a berth | `models.Booking` + `schemas.BookingCreate` |
| R3 | Represent non-vessel events occupying a berth | An event booking (no vessel) competes for the same berth/date resource | `models.BookingKind.EVENT`; both kinds share one table by design (README §Data model) |
| R4 | Detect double-bookings automatically | Two bookings with overlapping dates on the same berth are flagged, both at write time and retroactively across existing history | Unit-tested overlap logic (3 cases: sharing a day, adjacent-not-touching, one containing the other) + live 409 rejection + `/audit` re-derivation, independently recomputed and cross-checked against the ORM query (see §5) |
| R5 | Verify a vessel fits its assigned berth | Length is a hard fail if exceeded, a soft warning under the 10% margin rule; beam/draft hard-fail when known; unknown data is its own explicit state, never a silent pass | 7 unit tests in `test_conflicts.py`, plus a live before/after check: uploading real dimension data dropped fit warnings on the 1997 subset from 31 to 1, with the remaining one independently confirmed to be for the correct reason (unknown *berth* length, not vessel) |

Two more requirements I set for myself, because a system that only
satisfies R1–R5 checks boxes without being something a facility would
actually want to run:

| # | Requirement | Success criteria | How it was verified |
|---|---|---|---|
| R6 | The system must work against the *real* data, not just a clean mock | Import all 23 years without hand-fixing the source file first | Verified by running the importer against the unmodified source workbook and checking output counts/dates/names for corruption at each stage — this is what surfaced 7 real bugs (§6 of README) |
| R7 | A reviewer can run this with near-zero setup friction | `make import && make run` from a clean checkout, no manual steps | Verified by deleting `.venv` and `berths.db` and re-running from scratch; CI does the equivalent on every push |

## 3. Constraints

Things the design had to work within, not choices:

- **The source format is an unstructured, human-maintained spreadsheet
  grid**, not a normalized schema — berths as rows with the length
  embedded in free text, days as columns, occupant names as free text in
  cells. Nothing here is typed or validated at the source.
- **No vessel dimension data exists anywhere in the schedule itself.**
  LOA/beam/draft had to be modeled as legitimately absent, not defaulted.
- **The source format is not even internally consistent** — three
  different header/weekday/day-number row arrangements and two different
  berth-label conventions appear across the 23 years of the same
  workbook (README §The real data).
- **No stated concurrency or auth requirements.** The problem describes a
  single dock coordinator's workflow, not a multi-user system — SQLite
  and no-auth follow from that, not from avoiding the work.
- **No stated berth-occupancy model beyond "a boat is assigned to a
  berth."** Single-occupancy per berth per day was inferred (§4, A2),
  not given.

## 4. Assumptions

Explicit, because a wrong unstated assumption is worse than a documented
one:

- **A1 — Single occupancy.** A berth holds one vessel or event at a time.
  Inferred from the grid format (one name per cell); the "Small craft
  slips (institution boats)" berth's real data (several vessel names on
  one day) is a case where this assumption is visibly imperfect, and it's
  flagged rather than silently forced to fit.
- **A2 — One file/tab = one year.** Used to resolve headers that drop the
  year entirely ("January" instead of "JANUARY 2014"). Holds for all 23
  tabs in the source workbook; would need revisiting for a source that
  doesn't partition by year.
- **A3 — Unknown must be explicit, never guessed.** Applied uniformly:
  unknown vessel LOA, unknown berth length, an occupant cell with no
  berth label, a day-number row that doesn't start at 1 — each is
  surfaced as its own state (a warning, an `Unidentified` bucket, a
  skipped block) rather than defaulted to a value that could be wrong.
  This is the single assumption everything else in the design follows
  from.
- **A4 — The 10% length-margin rule is a recommendation, not a
  requirement.** Taken from the general maritime rule of thumb cited in
  the original brief's research links, not from a WHOI-specific policy
  (none was given) — implemented as a warning, not a hard block, for
  exactly that reason.
- **A5 — Unprefixed, non-keyword occupant text defaults to "vessel."**
  Vessels are the majority case in the data. This assumption was wrong
  often enough in practice (maintenance notes, scheduling annotations)
  that it drove real fixes — see §6 of the README — rather than staying
  a silent source of bad data.
- **A6 — The synthetic sample is representative enough to justify general
  fixes.** The parser fixes address *format* problems (header layout,
  label conventions, corrupted rows) that are properties of how the
  spreadsheet was built over 23 years, not one-off quirks of this
  specific synthetic dataset — so they're written as general rules
  ("a day-number row must start at 1"), not special cases keyed to
  specific years or values.

## 5. Limitations (known, not hidden)

- The vessel registry ("Science"/"Yachts" tabs) isn't parsed — most of
  the 484 imported vessels still have unknown dimensions.
- A pre-computed summary tab references a berth ("Marsh Landing") that
  never appears in any year's actual grid data — unresolved, flagged.
- ~61 rows (~2% of entries) sit in an ambiguous gap between two month
  blocks with no reliable way to assign them a date — dropped rather
  than guessed at.
- No authentication, no multi-user write-locking, no notifications.
- Single-occupancy berth model (A1) doesn't fully fit the one berth type
  that visibly hosts multiple simultaneous small craft.

## 6. Validating the logic, step by step

"It compiles and the demo looked right" is not validation. What was
actually done at each stage:

1. **Date-overlap logic** (`conflicts.date_ranges_overlap`) — isolated as
   a pure function specifically so it could be tested without a database;
   3 unit tests cover the boundary cases (sharing exactly one day,
   adjacent ranges that must *not* count as overlapping, one range fully
   containing another).
2. **Fit logic** (`conflicts.check_fit`) — also pure/DB-free; 7 unit tests
   cover every branch (hard length fail, soft margin warning, clean fit,
   beam fail, draft fail, unknown berth length, unknown vessel LOA) —
   including the two "unknown" branches added specifically because an
   early version didn't handle them and would have crashed or silently
   passed.
3. **The resolver's core algorithm**
   (`resolver.weighted_interval_schedule_keep`) — a hand-constructed test
   case where two possible outcomes exist (keep one long booking, or keep
   two shorter non-conflicting ones) with the expected winner computed by
   hand first, then asserted — not just "it returns something."
4. **The grid parser, per format variant** — each of the 5 real bugs
   found while scaling to 23 years (README §6) got its own regression
   test reproducing the *exact* failure condition in isolation (a
   synthetic 2-3 row CSV), not just a "does it run" smoke test. This is
   what caught the 2018-mislabeled-header bug's fix from silently
   introducing a new failure mode.
5. **Full import, at scale** — after every fix, re-ran the importer
   against all 23 real files and independently checked the output for
   corruption: vessel names that are suspiciously short/numeric (caught
   `1400`, `11`), booking dates outside the 1997–2019 range (caught the
   2018 mislabel), and berth codes that shouldn't exist (caught the
   phantom "July"/"August" berths). Each check was a fresh, separate
   query — not a re-run of the same assertion the fix was built to pass.
6. **The double-booking audit at scale** — the 9 conflicts found across
   23 years were re-derived with a standalone script using the same
   `date_ranges_overlap` primitive but a different traversal than the
   `/audit` endpoint's own code, specifically so a bug in one wouldn't be
   invisible to the other. Each of the 9 was then inspected individually
   (not just counted) — which is what surfaced that all 9 land on the
   `Unidentified` pseudo-berth, a finding that changes what the number
   means and is stated as a caveat in the README rather than left out.
7. **UI behavior** — verified by driving the actual running app in a
   browser (not just reading the JS): creating a booking, triggering a
   409 rejection, using the suggestion and force-override flow, running
   the audit, computing and applying a resolver plan and re-running the
   audit to confirm the conflict count actually dropped, searching and
   jumping to a result, and uploading a vessel-dimensions CSV and
   confirming the fit-warning count actually changed.
8. **CI** — verified by observing a real failure first (the first push's
   test run failed with "pytest: command not found" because it wasn't in
   `requirements.txt`), fixing the actual cause, and confirming a genuine
   green run afterward — not assumed to work because it was configured.
