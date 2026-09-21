from datetime import date
from typing import Optional

from pydantic import BaseModel, model_validator

from app.models import BerthLocation, BookingKind


class BerthCreate(BaseModel):
    code: str
    length_ft: float
    max_beam_ft: Optional[float] = None
    max_draft_ft: Optional[float] = None
    location: BerthLocation = BerthLocation.ALONGSIDE
    notes: Optional[str] = None


class BerthOut(BerthCreate):
    id: int

    class Config:
        from_attributes = True


class VesselCreate(BaseModel):
    name: str
    loa_ft: Optional[float] = None  # unknown for many historical/transient vessels
    beam_ft: Optional[float] = None
    draft_ft: Optional[float] = None
    vessel_type: Optional[str] = None
    is_guest: bool = False
    novice_crew: bool = False


class VesselOut(VesselCreate):
    id: int

    class Config:
        from_attributes = True


class BookingCreate(BaseModel):
    kind: BookingKind
    berth_id: int
    vessel_id: Optional[int] = None
    event_name: Optional[str] = None
    start_date: date
    end_date: date
    force: bool = False  # accept the booking despite non-fatal "warning" issues
    override_reason: Optional[str] = None  # required if force=True and there were warnings

    @model_validator(mode="after")
    def check_kind_fields(self):
        if self.kind == BookingKind.VESSEL and self.vessel_id is None:
            raise ValueError("vessel_id is required for kind='vessel'")
        if self.kind == BookingKind.EVENT and not self.event_name:
            raise ValueError("event_name is required for kind='event'")
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class BookingOut(BaseModel):
    id: int
    kind: BookingKind
    berth_id: int
    vessel_id: Optional[int]
    event_name: Optional[str]
    start_date: date
    end_date: date
    override_reason: Optional[str]

    class Config:
        from_attributes = True


class Issue(BaseModel):
    severity: str
    message: str


class BookingRejected(BaseModel):
    detail: str
    issues: list[Issue]
