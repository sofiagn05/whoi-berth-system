import calendar as calendar_module
from datetime import date as date_cls

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.conflicts import date_ranges_overlap, check_fit
from app.database import Base, SessionLocal, engine, get_db

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


# ---------- Bookings ----------

@app.get("/bookings", response_model=list[schemas.BookingOut])
def list_bookings(db: Session = Depends(get_db)):
    return db.query(models.Booking).all()


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
        d = max(booking.start_date, month_start)
        end = min(booking.end_date, month_end)
        while d <= end:
            if booking.berth_id in cells:
                cells[booking.berth_id][d.day].append({
                    "booking_id": booking.id, "kind": booking.kind, "name": occupant,
                })
            d = date_cls.fromordinal(d.toordinal() + 1)

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
        issues = check_fit(
            berth_length_ft=berth.length_ft,
            vessel_loa_ft=vessel.loa_ft,
            berth_max_beam_ft=berth.max_beam_ft,
            vessel_beam_ft=vessel.beam_ft,
            berth_max_draft_ft=berth.max_draft_ft,
            vessel_draft_ft=vessel.draft_ft,
        )
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


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
