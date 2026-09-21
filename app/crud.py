from sqlalchemy.orm import Session

from app import models
from app.conflicts import DimensionIssue, check_fit, date_ranges_overlap


class BookingRejectedError(Exception):
    def __init__(self, issues: list[DimensionIssue]):
        self.issues = issues
        super().__init__("; ".join(i.message for i in issues))


def fit_issues(berth: models.Berth, vessel: models.Vessel) -> list[DimensionIssue]:
    """Adapter over the pure conflicts.check_fit: unpacks the berth/vessel
    ORM objects once so callers don't each repeat the same six keyword args."""
    return check_fit(
        berth_length_ft=berth.length_ft,
        vessel_loa_ft=vessel.loa_ft,
        berth_max_beam_ft=berth.max_beam_ft,
        vessel_beam_ft=vessel.beam_ft,
        berth_max_draft_ft=berth.max_draft_ft,
        vessel_draft_ft=vessel.draft_ft,
    )


def find_double_bookings(db: Session, berth_id: int, start_date, end_date, exclude_booking_id=None):
    """Return existing bookings on this berth that overlap the given date range."""
    q = db.query(models.Booking).filter(models.Booking.berth_id == berth_id)
    if exclude_booking_id is not None:
        q = q.filter(models.Booking.id != exclude_booking_id)
    return [
        b for b in q.all()
        if date_ranges_overlap(start_date, end_date, b.start_date, b.end_date)
    ]


def evaluate_booking(db: Session, booking_in, exclude_booking_id=None) -> list[DimensionIssue]:
    """Run double-booking + physical-fit checks. Returns all issues found (empty = clean)."""
    issues: list[DimensionIssue] = []

    berth = db.query(models.Berth).get(booking_in.berth_id)
    if berth is None:
        raise ValueError(f"No such berth id {booking_in.berth_id}")

    overlaps = find_double_bookings(
        db, booking_in.berth_id, booking_in.start_date, booking_in.end_date, exclude_booking_id
    )
    for ob in overlaps:
        occupant = ob.event_name if ob.kind == models.BookingKind.EVENT else f"vessel #{ob.vessel_id}"
        issues.append(DimensionIssue(
            "error",
            f"Berth {berth.code} is already booked by {occupant} "
            f"from {ob.start_date} to {ob.end_date} (double-booking).",
        ))

    if booking_in.kind == models.BookingKind.VESSEL:
        vessel = db.query(models.Vessel).get(booking_in.vessel_id)
        if vessel is None:
            raise ValueError(f"No such vessel id {booking_in.vessel_id}")
        issues.extend(fit_issues(berth, vessel))

    return issues


def create_booking(db: Session, booking_in) -> models.Booking:
    issues = evaluate_booking(db, booking_in)
    hard_errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]

    if hard_errors:
        raise BookingRejectedError(hard_errors)
    if warnings and not booking_in.force:
        raise BookingRejectedError(warnings)

    booking = models.Booking(
        kind=booking_in.kind,
        berth_id=booking_in.berth_id,
        vessel_id=booking_in.vessel_id,
        event_name=booking_in.event_name,
        start_date=booking_in.start_date,
        end_date=booking_in.end_date,
        override_reason=booking_in.override_reason if warnings else None,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return booking


def score_berth_for_vessel(berth: models.Berth, vessel: models.Vessel) -> float:
    """Lower is better. Prefers an efficient (not wastefully oversized) fit,
    nudged toward channel-adjacent berths for deep-draft vessels and toward
    T-head berths for guest/novice crews (easier in/out)."""
    score = berth.length_ft - (vessel.loa_ft or 0)  # smaller slack = more efficient
    if (vessel.draft_ft or 0) >= 6 and berth.location == models.BerthLocation.CHANNEL_ADJACENT:
        score -= 100
    if (vessel.is_guest or vessel.novice_crew) and berth.location == models.BerthLocation.T_HEAD:
        score -= 50
    return score


def suggest_berths(db: Session, vessel: models.Vessel, start_date, end_date, limit: int = 5):
    """Rank available, physically-fitting berths for a vessel over a date range.

    Preference order:
      1. Must fit (no hard "error" issues) and be free for the whole range.
      2. Prefer the tightest reasonable margin (efficient use of space) among
         berths that satisfy the recommended 10% length margin.
      3. Deep-draft vessels (>= 6ft) are nudged toward channel-adjacent berths;
         guest/novice crews are nudged toward T-head berths (easier in/out).
    """
    if vessel.loa_ft is None:
        raise ValueError(f"Vessel '{vessel.name}' has no recorded LOA; add its dimensions first.")

    candidates = []
    for berth in db.query(models.Berth).all():
        if berth.length_ft is None:
            continue  # can't verify fit or score a berth of unknown length
        if find_double_bookings(db, berth.id, start_date, end_date):
            continue
        issues = fit_issues(berth, vessel)
        if any(i.severity == "error" for i in issues):
            continue

        candidates.append((score_berth_for_vessel(berth, vessel), berth, issues))

    candidates.sort(key=lambda c: c[0])
    return [(berth, issues) for _, berth, issues in candidates[:limit]]
