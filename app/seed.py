"""Populate berths.db with a small, realistic mock dataset.

Run with: python -m app.seed
Includes a couple of intentional double-bookings and a fit violation so the
/audit endpoint and the UI's warning states have something to demonstrate.
"""

from datetime import date

from app.database import Base, SessionLocal, engine
from app.models import Berth, BerthLocation, Booking, BookingKind, Vessel


def run():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    if db.query(Berth).first():
        print("Database already has data; skipping seed.")
        return

    berths = [
        Berth(code="A1", length_ft=30, max_beam_ft=12, max_draft_ft=5, location=BerthLocation.INNER),
        Berth(code="A2", length_ft=35, max_beam_ft=13, max_draft_ft=6, location=BerthLocation.INNER),
        Berth(code="B1", length_ft=45, max_beam_ft=15, max_draft_ft=8, location=BerthLocation.ALONGSIDE),
        Berth(code="B2", length_ft=50, max_beam_ft=16, max_draft_ft=9, location=BerthLocation.ALONGSIDE),
        Berth(code="C1", length_ft=70, max_beam_ft=20, max_draft_ft=12, location=BerthLocation.CHANNEL_ADJACENT),
        Berth(code="T1", length_ft=60, max_beam_ft=18, max_draft_ft=10, location=BerthLocation.T_HEAD),
    ]
    db.add_all(berths)
    db.commit()

    vessels = [
        Vessel(name="Gull", loa_ft=28, beam_ft=10, draft_ft=4, vessel_type="sailboat"),
        Vessel(name="Osprey", loa_ft=33, beam_ft=11, draft_ft=5, vessel_type="sailboat"),
        Vessel(name="Nor'easter", loa_ft=42, beam_ft=14, draft_ft=7, vessel_type="sailboat"),
        Vessel(name="Research I", loa_ft=65, beam_ft=18, draft_ft=11, vessel_type="research vessel"),
        Vessel(name="Guest Catamaran", loa_ft=38, beam_ft=17, draft_ft=3, vessel_type="catamaran", is_guest=True),
        Vessel(name="Community Skiff", loa_ft=18, beam_ft=7, draft_ft=2, vessel_type="dinghy",
               is_guest=True, novice_crew=True),
    ]
    db.add_all(vessels)
    db.commit()

    bookings = [
        Booking(kind=BookingKind.VESSEL, berth_id=berths[0].id, vessel_id=vessels[0].id,
                 start_date=date(2026, 6, 1), end_date=date(2026, 6, 10)),
        Booking(kind=BookingKind.VESSEL, berth_id=berths[2].id, vessel_id=vessels[2].id,
                 start_date=date(2026, 6, 1), end_date=date(2026, 6, 20)),
        # Intentional double-booking on C1: two overlapping bookings.
        Booking(kind=BookingKind.VESSEL, berth_id=berths[4].id, vessel_id=vessels[3].id,
                 start_date=date(2026, 7, 1), end_date=date(2026, 7, 15)),
        Booking(kind=BookingKind.EVENT, berth_id=berths[4].id, event_name="Community Sail Day",
                 start_date=date(2026, 7, 10), end_date=date(2026, 7, 10)),
        # Intentional fit violation: Osprey (33ft) jammed into A1 (30ft) -> hard error, saved directly via ORM.
        Booking(kind=BookingKind.VESSEL, berth_id=berths[0].id, vessel_id=vessels[1].id,
                 start_date=date(2026, 8, 1), end_date=date(2026, 8, 5)),
        Booking(kind=BookingKind.EVENT, berth_id=berths[5].id, event_name="Community Sail Day",
                 start_date=date(2026, 9, 5), end_date=date(2026, 9, 5)),
    ]
    db.add_all(bookings)
    db.commit()
    db.close()
    print("Seeded berths.db with mock berths, vessels, and bookings (including intentional conflicts).")


if __name__ == "__main__":
    run()
