import enum

from sqlalchemy import Boolean, Column, Date, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database import Base


class BerthLocation(str, enum.Enum):
    T_HEAD = "t_head"          # easy in/out, good for novices / large yachts
    ALONGSIDE = "alongside"    # standard side-tie
    INNER = "inner"            # sheltered, shallower, harder to maneuver into
    CHANNEL_ADJACENT = "channel_adjacent"  # deep water, closest to main channel


class BookingKind(str, enum.Enum):
    VESSEL = "vessel"
    EVENT = "event"


class Berth(Base):
    __tablename__ = "berths"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True, nullable=False)
    length_ft = Column(Float, nullable=True)  # unknown for berths recovered from an unlabeled source row
    max_beam_ft = Column(Float, nullable=True)
    max_draft_ft = Column(Float, nullable=True)
    location = Column(Enum(BerthLocation), default=BerthLocation.ALONGSIDE, nullable=False)
    notes = Column(String, nullable=True)

    bookings = relationship("Booking", back_populates="berth")


class Vessel(Base):
    __tablename__ = "vessels"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    loa_ft = Column(Float, nullable=True)  # unknown for many historical/transient vessels
    beam_ft = Column(Float, nullable=True)
    draft_ft = Column(Float, nullable=True)
    vessel_type = Column(String, nullable=True)  # e.g. sailboat, catamaran, powerboat
    is_guest = Column(Boolean, default=False)     # outside/guest boat vs. resident fleet
    novice_crew = Column(Boolean, default=False)  # for maneuverability-aware placement

    bookings = relationship("Booking", back_populates="vessel")


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(Enum(BookingKind), nullable=False)

    berth_id = Column(Integer, ForeignKey("berths.id"), nullable=False)
    vessel_id = Column(Integer, ForeignKey("vessels.id"), nullable=True)  # null for events

    event_name = Column(String, nullable=True)  # e.g. "Community Sail Day" (null for vessels)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)  # inclusive

    override_reason = Column(String, nullable=True)  # set if a warning was force-accepted

    berth = relationship("Berth", back_populates="bookings")
    vessel = relationship("Vessel", back_populates="bookings")
