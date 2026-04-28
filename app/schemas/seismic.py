from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class SeismicEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: Optional[str] = None
    event_id: str
    magnitude: Optional[float] = None
    mag_type: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    depth: Optional[float] = None
    event_type: Optional[str] = None
    event_date: Optional[datetime] = None
    evaluation_status: Optional[str] = None
    rms: Optional[float] = None
    # TexNet-specific
    phase_count: Optional[int] = None
    region_name: Optional[str] = None
    county_name: Optional[str] = None
    station_count: Optional[int] = None
    # USGS-specific
    place: Optional[str] = None
    title: Optional[str] = None
    alternate_ids: Optional[str] = None
    gap: Optional[float] = None


class SeismicFetchResult(BaseModel):
    """Shared response schema for all seismic fetch endpoints."""
    status: str          # success | failed
    source: str          # texnet | usgs
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
