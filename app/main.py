import calendar as calendar_module
import csv
import io
from datetime import date as date_cls

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import analytics, crud, models, resolver, schemas
from app.conflicts import date_ranges_overlap, iter_days
from app.database import Base, engine, get_db

Base.metadata.create_all(bind=engine)

app = FastAPI(title="WHOI Berth Reservation System")


# ---------- Berths ----------

@app.post("/berths", response_model=schemas.BerthOut)
def create_berth(berth: schemas.BerthCreate, db: Session = Depends(get_db)):
    db_berth = models.Berth(**berth.model_dump())
    db.add(db_berth)
    db.commit()
    db.refresh(db_berth)
    return db_berth


@app.get("/berths", response_model=list[schemas.BerthOut])
def list_berths(db: Session = Depends(get_db)):
    return db.query(models.Berth).all()


# ---------- Vessels ----------

@app.post("/vessels", response_model=schemas.VesselOut)
def create_vessel(vessel: schemas.VesselCreate, db: Session = Depends(get_db)):
    db_vessel = models.Vessel(**vessel.model_dump())
    db.add(db_vessel)
    db.commit()
    db.refresh(db_vessel)
    return db_vessel


@app.get("/vessels", response_model=list[schemas.VesselOut])
def list_vessels(db: Session = Depends(get_db)):
    return db.query(models.Vessel).all()


@app.post("/vessels/import")
async def import_vessel_dimensions(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Bulk-fill LOA/beam/draft for existing vessels from a CSV
    (columns: name, loa_ft, beam_ft, draft_ft, vessel_type -- only name is
    required, the rest fill in whatever's present). Matches by name,
    case-insensitively; does not create new vessels, since a name typo in
    the CSV would otherwise silently create a duplicate instead of updating
    the real one.
    """
    raw = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    vessels_by_name = {v.name.lower(): v for v in db.query(models.Vessel).all()}

    updated, not_found = [], []
    for row in reader:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        vessel = vessels_by_name.get(name.lower())
        if vessel is None:
            not_found.append(name)
            continue
        for field in ("loa_ft", "beam_ft", "draft_ft"):
            value = (row.get(field) or "").strip()
            if value:
                setattr(vessel, field, float(value))
        vessel_type = (row.get("vessel_type") or "").strip()
        if vessel_type:
            vessel.vessel_type = vessel_type
        updated.append(vessel.name)

    db.commit()
    return {"updated": updated, "not_found": not_found}


# ---------- Bookings ----------

@app.get("/bookings", response_model=list[schemas.BookingOut])
def list_bookings(db: Session = Depends(get_db)):
    return db.query(models.Booking).all()


@app.get("/bookings/search")
def search_bookings(q: str, db: Session = Depends(get_db)):
    """Find bookings by vessel or event name -- a substring, case-insensitive
    match. With years of history, browsing month by month to find "when did
    this vessel last berth here" doesn't scale; this does the lookup
    directly instead.
    """
    q = q.strip()
    if not q:
        return []

    berths_by_id = {b.id: b for b in db.query(models.Berth).all()}
    vessels_by_id = {v.id: v for v in db.query(models.Vessel).all()}
    needle = q.lower()

    results = []
    for b in db.query(models.Booking).all():
        name = vessels_by_id[b.vessel_id].name if b.kind == models.BookingKind.VESSEL and b.vessel_id in vessels_by_id else b.event_name
        if name and needle in name.lower():
            berth = berths_by_id.get(b.berth_id)
            results.append({
                "booking_id": b.id,
                "occupant": name,
                "kind": b.kind,
                "berth_code": berth.code if berth else None,
                "start_date": str(b.start_date),
                "end_date": str(b.end_date),
                "year": b.start_date.year,
                "month": b.start_date.month,
            })

    results.sort(key=lambda r: r["start_date"])
    return results


@app.post("/bookings", response_model=schemas.BookingOut)
def create_booking(booking: schemas.BookingCreate, db: Session = Depends(get_db)):
    try:
        return crud.create_booking(db, booking)
    except crud.BookingRejectedError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "Booking rejected",
                "issues": [{"severity": i.severity, "message": i.message} for i in e.issues],
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/bookings/{booking_id}")
def delete_booking(booking_id: int, db: Session = Depends(get_db)):
    booking = db.query(models.Booking).get(booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    db.delete(booking)
    db.commit()
    return {"ok": True}


@app.get("/bookings/suggest")
def suggest_berth(vessel_id: int, start_date: str, end_date: str, db: Session = Depends(get_db)):
    vessel = db.query(models.Vessel).get(vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404, detail="Vessel not found")
    sd, ed = date_cls.fromisoformat(start_date), date_cls.fromisoformat(end_date)
    try:
        results = crud.suggest_berths(db, vessel, sd, ed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return [
        {
            "berth": schemas.BerthOut.model_validate(berth),
            "warnings": [{"severity": i.severity, "message": i.message} for i in issues],
        }
        for berth, issues in results
    ]


# ---------- Calendar grid: the computerized replacement for the paper/Excel grid ----------

@app.get("/calendar")
def calendar_grid(year: int, month: int, db: Session = Depends(get_db)):
    """Returns one cell per (berth, day) for the given month, mirroring the
    original spreadsheet grid layout but with occupants resolved from the
    normalized Booking table and same-day conflicts flagged automatically.
    """
    days_in_month = calendar_module.monthrange(year, month)[1]
    month_start = date_cls(year, month, 1)
    month_end = date_cls(year, month, days_in_month)

    berths = db.query(models.Berth).order_by(models.Berth.code).all()
    bookings = (
        db.query(models.Booking)
        .filter(models.Booking.start_date <= month_end, models.Booking.end_date >= month_start)
        .all()
    )
    vessel_names = {v.id: v.name for v in db.query(models.Vessel).all()}

    cells: dict[int, dict[int, list]] = {b.id: {d: [] for d in range(1, days_in_month + 1)} for b in berths}
    for booking in bookings:
        occupant = vessel_names.get(booking.vessel_id) if booking.kind == models.BookingKind.VESSEL else booking.event_name
        clipped_start = max(booking.start_date, month_start)
        clipped_end = min(booking.end_date, month_end)
        if booking.berth_id in cells:
            for d in iter_days(clipped_start, clipped_end):
                cells[booking.berth_id][d.day].append({
                    "booking_id": booking.id, "kind": booking.kind, "name": occupant,
                    "priority": booking.priority,
                })

    rows = []
    for b in berths:
        day_cells = cells[b.id]
        rows.append({
            "berth_id": b.id,
            "berth_code": b.code,
            "berth_length_ft": b.length_ft,
            "days": [
                {"day": d, "occupants": day_cells[d], "conflict": len(day_cells[d]) > 1}
                for d in range(1, days_in_month + 1)
            ],
        })

    return {"year": year, "month": month, "days_in_month": days_in_month, "rows": rows}


# ---------- Audit: find every existing conflict/fit violation in the data ----------

@app.get("/audit")
def audit(db: Session = Depends(get_db)):
    """Scan ALL bookings for double-bookings and berth/vessel fit violations.

    This is the automated replacement for manually checking a grid by eye.
    """
    bookings = db.query(models.Booking).order_by(models.Booking.berth_id, models.Booking.start_date).all()
    double_bookings = []
    fit_violations = []

    by_berth: dict[int, list[models.Booking]] = {}
    for b in bookings:
        by_berth.setdefault(b.berth_id, []).append(b)

    for berth_id, blist in by_berth.items():
        for i in range(len(blist)):
            for j in range(i + 1, len(blist)):
                a, b = blist[i], blist[j]
                if date_ranges_overlap(a.start_date, a.end_date, b.start_date, b.end_date):
                    # Suggest where the later-added booking could move instead,
                    # so a conflict found in 23 years of history isn't just
                    # flagged -- it comes with an actionable fix.
                    suggestion = None
                    if b.kind == models.BookingKind.VESSEL and b.vessel_id is not None:
                        vessel = db.query(models.Vessel).get(b.vessel_id)
                        if vessel is not None and vessel.loa_ft is not None:
                            alternatives = crud.suggest_berths(db, vessel, b.start_date, b.end_date, limit=1)
                            alternatives = [(berth, issues) for berth, issues in alternatives if berth.id != berth_id]
                            if alternatives:
                                suggestion = alternatives[0][0].code

                    double_bookings.append({
                        "berth_id": berth_id,
                        "booking_a": a.id,
                        "booking_b": b.id,
                        "dates_a": [str(a.start_date), str(a.end_date)],
                        "dates_b": [str(b.start_date), str(b.end_date)],
                        "suggested_alternative_for_b": suggestion,
                    })

    for b in bookings:
        if b.kind != models.BookingKind.VESSEL or b.vessel_id is None:
            continue
        berth = db.query(models.Berth).get(b.berth_id)
        vessel = db.query(models.Vessel).get(b.vessel_id)
        if berth is None or vessel is None:
            continue
        issues = crud.fit_issues(berth, vessel)
        for issue in issues:
            fit_violations.append({
                "booking_id": b.id,
                "berth_code": berth.code,
                "vessel_name": vessel.name,
                "severity": issue.severity,
                "message": issue.message,
            })

    return {
        "double_bookings": double_bookings,
        "fit_violations": fit_violations,
        "summary": {
            "total_bookings": len(bookings),
            "double_booking_pairs": len(double_bookings),
            "fit_errors": len([f for f in fit_violations if f["severity"] == "error"]),
            "fit_warnings": len([f for f in fit_violations if f["severity"] == "warning"]),
        },
    }


# ---------- Analytics: the dashboard's data source ----------

@app.get("/analytics")
def get_analytics(db: Session = Depends(get_db)):
    return analytics.compute_analytics(db)


# ---------- Batch conflict resolution ----------

@app.get("/audit/resolve")
def preview_resolution(db: Session = Depends(get_db)):
    """Read-only: computes a reassignment plan for every conflict in the
    system at once, without writing anything."""
    return resolver.build_resolution_plan(db)


@app.post("/audit/resolve/apply")
def apply_resolution(moves: list[dict], db: Session = Depends(get_db)):
    """Commits a plan's moves (as returned by GET /audit/resolve) by
    updating each booking's berth_id."""
    applied = resolver.apply_resolution_plan(db, moves)
    return {"applied": applied}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
