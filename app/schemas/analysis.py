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
    # earliest H-10 record within the search window — used to estimate injection duration
    first_report_date: Optional[datetime] = None
    last_report_date: Optional[datetime] = None
    # ratio of mean monthly injection in last 3 months vs prior 9 months
    # >1.0 = ramp-up, <1.0 = ramp-down, None = insufficient data
    rate_change_ratio: Optional[float] = None
    # formation-specific fields populated by PhysicsAttributionService
    formation_name: Optional[str] = None
    d_m2_s_used: Optional[float] = None   # hydraulic diffusivity used in Theis calculation
    cff_weight: Optional[float] = None    # Coulomb failure weight from avg_pressure_psi


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
    depth_source: Optional[str] = None   # "tvd" | "falldepth" | "basin_default"


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


class FracPriorParams(BaseModel):
    """Parameters of the Monte Carlo frac prior, either fitted from FracFocus data or
    falling back to published Delaware Basin literature defaults."""
    source: str               # "data_driven" | "basin_defaults"
    sample_size: int          # number of FracFocus rows used to fit the distribution
    n_jobs_mean: float        # Poisson λ for synthetic job count within search area
    water_vol_log_mean: float # location of log-normal distribution (ln bbl)
    water_vol_log_std: float  # scale  of log-normal distribution
    depth_mean_ft: float      # mean TVD (feet) for synthetic jobs
    depth_std_ft: float       # std  TVD (feet)


class AttributionResult(BaseModel):
    engine: str
    likely_driver: str           # "swd" | "frac" | "indeterminate"
    confidence: float            # P(driver is correct): 0.5 = coin-flip, 1.0 = fully one-sided; 0.0 = no evidence
    swd_score: float
    frac_score: float
    signals: list[AttributionSignal]
    # --- Monte Carlo frac uncertainty fields (populated only when frac data is absent) ---
    frac_data_quality: str = "observed"       # "observed" | "absent"
    mc_frac_score_mean: Optional[float] = None
    mc_frac_score_p5:   Optional[float] = None
    mc_frac_score_p95:  Optional[float] = None
    # adjusted_* uses mc_frac_score_mean in the verdict instead of the observed zero
    adjusted_likely_driver: Optional[str]   = None
    adjusted_confidence:    Optional[float] = None
    # Physics enhancement flags
    cff_applied: bool = False    # True when Coulomb boost was applied to any SWD well


class SequenceStatsOut(BaseModel):
    """Seismic sequence statistics for events near the analysed event."""
    n_events: int
    b_value: Optional[float] = None
    omori_p: Optional[float] = None
    interevent_cv: Optional[float] = None
    cusum_peak: Optional[float] = None
    n_background: Optional[int] = None    # ETAS-classified background events
    n_triggered: Optional[int] = None     # ETAS-classified triggered (aftershock) events
    background_fraction: Optional[float] = None
    mc_used: float = 2.0
    radius_km: float = 20.0
    window_days: int = 365


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
    # MC prior params set only when nearby_frac_jobs is empty
    frac_prior_params: Optional[FracPriorParams] = None


class EventAnalysisOut(BaseModel):
    snapshot_id: int
    context: EventContextOut
    attribution: AttributionResult
    sequence_stats: Optional[SequenceStatsOut] = None
