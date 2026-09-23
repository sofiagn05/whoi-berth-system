"""Batch conflict resolution: given every existing double-booking in the
system at once, compute a reassignment that clears as many as possible.

This goes beyond the one-at-a-time "here's a conflict, here's one
alternative berth" flow in the audit endpoint: it looks at the whole
schedule, decides which of a berth's conflicting reservations should stay
put, and finds a new berth for the rest -- globally, in one pass.

Two stages:

1. Per berth, `weighted_interval_schedule_keep` decides which of that
   berth's (possibly mutually overlapping) reservations to keep in place.
   This is the classic weighted interval scheduling DP (Kleinberg & Tardos):
   for a single resource, it is the *optimal* choice of non-overlapping
   reservations maximizing total kept weight. Weight is priority-first,
   duration-second (`_booking_weight`): a CRITICAL research mission is
   never bumped for a ROUTINE booking regardless of length, and among
   reservations of equal priority a 15-day cruise is preferred over a
   1-day community sail day it conflicts with -- plain "maximize count"
   would prefer the opposite, since dropping the long booking frees more
   slots.

2. Whatever gets displaced is greedily reassigned to the best available,
   physically-fitting berth elsewhere (reusing the same scoring heuristic
   as the single-booking suggestion endpoint), tracking a live occupancy
   timeline per berth so two displaced bookings can't collide with each
   other either. Anything that can't be placed is reported, not guessed at.
"""

from bisect import bisect_left
from datetime import date

from sqlalchemy.orm import Session

from app import crud, models
from app.conflicts import date_ranges_overlap
from app.models import PRIORITY_RANK

# Large enough that priority tier always dominates the duration tiebreaker,
# for any booking length this system will realistically see (even a
# multi-year reservation is orders of magnitude under this). A CRITICAL
# two-day research cruise must outrank a ROUTINE three-week guest booking
# it conflicts with -- not the other way around.
PRIORITY_WEIGHT = 1_000_000


def _booking_weight(b) -> int:
    duration_days = (b.end_date - b.start_date).days + 1
    return PRIORITY_RANK[b.priority] * PRIORITY_WEIGHT + duration_days


def weighted_interval_schedule_keep(bookings: list):
    """Optimal (by total weight kept) selection of non-overlapping bookings
    from a set that all compete for the same single-occupancy resource.

    Weight is priority-first, duration-second (see _booking_weight): two
    bookings of equal priority still resolve the way the original
    duration-only version did (the longer one wins), but a higher-priority
    booking is never displaced in favor of a lower-priority one, regardless
    of length. Returns (kept, displaced)."""
    if not bookings:
        return [], []

    ordered = sorted(bookings, key=lambda b: (b.end_date, b.start_date))
    n = len(ordered)
    starts = [b.start_date for b in ordered]
    ends = [b.end_date for b in ordered]
    weights = [_booking_weight(b) for b in ordered]

    # p[i] = index (0-based) of the latest booking that ends before booking i
    # starts, or -1 if none. ends[0:i] is sorted ascending, so binary search.
    p = [bisect_left(ends, starts[i], 0, i) - 1 for i in range(n)]

    opt = [0] * (n + 1)  # opt[i] = best total weight using ordered[0:i]
    for i in range(1, n + 1):
        include = weights[i - 1] + (opt[p[i - 1] + 1] if p[i - 1] >= 0 else 0)
        exclude = opt[i - 1]
        opt[i] = max(include, exclude)

    kept_idx = set()
    i = n
    while i > 0:
        include = weights[i - 1] + (opt[p[i - 1] + 1] if p[i - 1] >= 0 else 0)
        if include >= opt[i - 1]:
            kept_idx.add(i - 1)
            i = p[i - 1] + 1
        else:
            i -= 1

    kept = [ordered[i] for i in range(n) if i in kept_idx]
    displaced = [ordered[i] for i in range(n) if i not in kept_idx]
    return kept, displaced


def build_resolution_plan(db: Session) -> dict:
    """Read-only: computes the plan without writing anything to the DB."""
    bookings = db.query(models.Booking).all()
    berths = db.query(models.Berth).all()
    berth_by_id = {b.id: b for b in berths}

    by_berth: dict[int, list] = {}
    for b in bookings:
        by_berth.setdefault(b.berth_id, []).append(b)

    all_kept, all_displaced = [], []
    for blist in by_berth.values():
        kept, displaced = weighted_interval_schedule_keep(blist)
        all_kept.extend(kept)
        all_displaced.extend(displaced)

    occupancy: dict[int, list[tuple[date, date]]] = {b.id: [] for b in berths}
    for b in all_kept:
        occupancy[b.berth_id].append((b.start_date, b.end_date))

    moves, unresolved = [], []
    for booking in sorted(all_displaced, key=lambda b: b.start_date):
        vessel = None
        if booking.kind == models.BookingKind.VESSEL and booking.vessel_id:
            vessel = db.query(models.Vessel).get(booking.vessel_id)
        occupant_name = vessel.name if vessel else booking.event_name

        candidates = []
        for berth in berths:
            if berth.id == booking.berth_id:
                continue
            if berth.length_ft is None:
                continue  # can't verify fit or score a berth of unknown length as a target
            if any(date_ranges_overlap(booking.start_date, booking.end_date, s, e)
                   for s, e in occupancy[berth.id]):
                continue

            needs_review = False
            if booking.kind == models.BookingKind.VESSEL:
                if vessel is None:
                    continue
                if vessel.loa_ft is None:
                    needs_review = True
                    score = berth.length_ft
                else:
                    if any(i.severity == "error" for i in crud.fit_issues(berth, vessel)):
                        continue
                    score = crud.score_berth_for_vessel(berth, vessel)
            else:
                score = berth.length_ft  # events: no fit constraint, prefer smaller berths free

            candidates.append((score, berth, needs_review))

        candidates.sort(key=lambda c: c[0])
        if candidates:
            _, chosen, needs_review = candidates[0]
            occupancy[chosen.id].append((booking.start_date, booking.end_date))
            moves.append({
                "booking_id": booking.id,
                "occupant": occupant_name,
                "from_berth_id": booking.berth_id,
                "from_berth": berth_by_id[booking.berth_id].code,
                "to_berth_id": chosen.id,
                "to_berth": chosen.code,
                "dates": [str(booking.start_date), str(booking.end_date)],
                "needs_review": needs_review,
            })
        else:
            unresolved.append({
                "booking_id": booking.id,
                "occupant": occupant_name,
                "berth": berth_by_id[booking.berth_id].code,
                "dates": [str(booking.start_date), str(booking.end_date)],
                "reason": "No available, fitting berth found for these dates.",
            })

    return {
        "kept_count": len(all_kept),
        "moves": moves,
        "unresolved": unresolved,
    }


def apply_resolution_plan(db: Session, moves: list[dict]) -> int:
    applied = 0
    for m in moves:
        booking = db.query(models.Booking).get(m["booking_id"])
        if booking is None:
            continue
        booking.berth_id = m["to_berth_id"]
        applied += 1
    db.commit()
    return applied
