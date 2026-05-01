from datetime import datetime
from sqlalchemy import Column, Float, Integer, Text, DateTime, UniqueConstraint
from app.core.database import Base


class EventContextSnapshot(Base):
    """Persisted per-run analysis snapshot. One row per (event_id, run_timestamp).
    Stores both the search-window parameters and the heuristic attribution result so
    every run is auditable and replayable. signals_json holds the serialised list of
    AttributionSignal objects that explain the driver call."""
    __tablename__ = "event_context_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Text, nullable=False, index=True)
    run_timestamp = Column(DateTime, nullable=False, index=True)
    # search-window parameters used for this run
    swd_radius_km = Column(Float, nullable=False)
    swd_window_days = Column(Integer, nullable=False)
    frac_radius_km = Column(Float, nullable=False)
    frac_window_days = Column(Integer, nullable=False)
    station_radius_km = Column(Float, nullable=False)
    # attribution output
    engine = Column(Text, nullable=False)           # e.g. "heuristic_v0"
    likely_driver = Column(Text, nullable=False)    # "swd" | "frac" | "indeterminate"
    confidence = Column(Float, nullable=False)       # 0.0 – 1.0
    signals_json = Column(Text, nullable=True)       # JSON-serialised list[AttributionSignal]
    # nearby context counts
    nearby_swd_count = Column(Integer, nullable=False, default=0)
    nearby_frac_count = Column(Integer, nullable=False, default=0)
    nearby_station_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("event_id", "run_timestamp", name="uq_snapshot_event_run"),
    )
