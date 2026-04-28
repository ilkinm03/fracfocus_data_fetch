from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class SeismicEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    magnitude: Optional[float] = None
    mag_type: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    depth: Optional[float] = None
    phase_count: Optional[int] = None
    event_type: Optional[str] = None
    region_name: Optional[str] = None
    event_date: Optional[datetime] = None
    evaluation_status: Optional[str] = None
    county_name: Optional[str] = None
    rms: Optional[float] = None
    station_count: Optional[int] = None


class TexNetFetchResult(BaseModel):
    status: str  # success | failed
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    pages: int = 0
    error: Optional[str] = None


class SeismicEventListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SeismicEventOut]