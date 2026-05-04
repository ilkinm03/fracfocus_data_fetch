from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class NearbySWDWell(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uic_number: str
    api_no: Optional[str] = None
    distance_km: float
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    top_inj_zone: Optional[float] = None
    bot_inj_zone: Optional[float] = None
    # H-10 summary within the time window
    monthly_record_count: int = 0
    cumulative_bbl: float = 0.0
    avg_pressure_psi: Optional[float] = None
    max_pressure_psi: Optional[float] = None
    last_report_date: Optional[datetime] = None
    # ratio of mean monthly injection in last 3 months vs prior 9 months
    # >1.0 = ramp-up, <1.0 = ramp-down, None = insufficient data
    rate_change_ratio: Optional[float] = None


class NearbyFracJob(BaseModel):
    api_number: Optional[str] = None
    distance_km: float
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    job_start_date: Optional[str] = None
    job_end_date: Optional[str] = None
    operator_name: Optional[str] = None
    well_name: Optional[str] = None
    total_water_volume: Optional[float] = None
    formation_depth: Optional[float] = None


class NearbyStation(BaseModel):
    network_station: str
    network: str
    station_code: str
    distance_km: float
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    site_name: Optional[str] = None
    end_time: Optional[datetime] = None


class AttributionSignal(BaseModel):
    name: str
    value: float
    unit: str
    description: str


class AttributionResult(BaseModel):
    engine: str
    likely_driver: str           # "swd" | "frac" | "indeterminate"
    confidence: float            # P(driver is correct): 0.5 = coin-flip, 1.0 = fully one-sided; 0.0 = no evidence
    swd_score: float
    frac_score: float
    signals: list[AttributionSignal]


class EventContextOut(BaseModel):
    event_id: str
    event_latitude: Optional[float]
    event_longitude: Optional[float]
    event_depth_km: Optional[float]
    event_date: Optional[datetime]
    event_magnitude: Optional[float]
    # search parameters used
    swd_radius_km: float
    swd_window_days: int
    frac_radius_km: float
    frac_window_days: int
    station_radius_km: float
    # nearby context
    nearby_swd_wells: list[NearbySWDWell]
    nearby_frac_jobs: list[NearbyFracJob]
    nearby_stations: list[NearbyStation]


class EventAnalysisOut(BaseModel):
    snapshot_id: int
    context: EventContextOut
    attribution: AttributionResult
