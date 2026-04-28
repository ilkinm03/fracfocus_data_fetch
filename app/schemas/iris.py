from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class IRISStationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    network_station: str
    network: str
    station_code: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    elevation: Optional[float] = None
    site_name: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class IRISFetchResult(BaseModel):
    status: str          # success | failed
    source: str = "iris"
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    error: Optional[str] = None


class IRISStationListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[IRISStationOut]
