"""Aggregate statistics over the whole booking history: berth utilization,
conflict frequency over time, and busiest vessels. Powers the dashboard.
"""

from collections import defaultdict

from sqlalchemy.orm import Session

from app import models
from app.conflicts import iter_days


def compute_analytics(db: Session) -> dict:
    bookings = db.query(models.Booking).all()
    berths = db.query(models.Berth).all()
    vessels = {v.id: v for v in db.query(models.Vessel).all()}

    if not bookings or not berths:
        return {
            "window": None, "utilization": [], "conflicts_by_month": [],
            "top_vessels": [], "summary": {"total_bookings": len(bookings)},
        }

    window_start = min(b.start_date for b in bookings)
    window_end = max(b.end_date for b in bookings)
    total_days = (window_end - window_start).days + 1

    by_berth = defaultdict(list)
    for b in bookings:
        by_berth[b.berth_id].append(b)

    utilization = []
    conflicts_by_month = defaultdict(int)
    for berth in berths:
        blist = by_berth.get(berth.id, [])
        day_counts = defaultdict(int)
        for b in blist:
            for d in iter_days(b.start_date, b.end_date):
                day_counts[d] += 1

        occupied_days = len(day_counts)
        occupied_day_instances = sum(day_counts.values())
        for d, count in day_counts.items():
            if count > 1:
                conflicts_by_month[f"{d.year}-{d.month:02d}"] += 1

        utilization.append({
            "berth_id": berth.id,
            "berth_code": berth.code,
            "booking_count": len(blist),
            "occupied_days": occupied_days,
            "double_booked_days": occupied_day_instances - occupied_days,
            "total_days": total_days,
            "utilization_pct": round(100 * occupied_days / total_days, 1) if total_days else 0.0,
        })

    utilization.sort(key=lambda u: -u["utilization_pct"])

    conflicts_series = [
        {"month": k, "conflict_days": v} for k, v in sorted(conflicts_by_month.items())
    ]

    vessel_stats = defaultdict(lambda: {"booking_count": 0, "total_days": 0})
    for b in bookings:
        if b.kind == models.BookingKind.VESSEL and b.vessel_id in vessels:
            vs = vessel_stats[b.vessel_id]
            vs["booking_count"] += 1
            vs["total_days"] += (b.end_date - b.start_date).days + 1
    top_vessels = sorted(
        [{"name": vessels[vid].name, **stats} for vid, stats in vessel_stats.items()],
        key=lambda v: -v["total_days"],
    )[:8]

    return {
        "window": {"start": str(window_start), "end": str(window_end), "total_days": total_days},
        "utilization": utilization,
        "conflicts_by_month": conflicts_series,
        "top_vessels": top_vessels,
        "summary": {
            "total_bookings": len(bookings),
            "busiest_berth": utilization[0]["berth_code"] if utilization else None,
            "busiest_berth_pct": utilization[0]["utilization_pct"] if utilization else None,
            "total_conflict_days": sum(conflicts_by_month.values()),
        },
    }
