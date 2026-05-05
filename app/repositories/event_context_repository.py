from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from app.models.event_context import EventContextSnapshot


class EventContextRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def save_snapshot(
        self,
        event_id: str,
        run_timestamp: datetime,
        swd_radius_km: float,
        swd_window_days: int,
        frac_radius_km: float,
        frac_window_days: int,
        station_radius_km: float,
        engine: str,
        likely_driver: str,
        confidence: float,
        signals_json: Optional[str],
        nearby_swd_count: int,
        nearby_frac_count: int,
        nearby_station_count: int,
        frac_data_quality: Optional[str] = None,
        mc_frac_score_mean: Optional[float] = None,
        mc_frac_score_p5: Optional[float] = None,
        mc_frac_score_p95: Optional[float] = None,
        adjusted_likely_driver: Optional[str] = None,
        adjusted_confidence: Optional[float] = None,
    ) -> EventContextSnapshot:
        snap = EventContextSnapshot(
            event_id=event_id,
            run_timestamp=run_timestamp,
            swd_radius_km=swd_radius_km,
            swd_window_days=swd_window_days,
            frac_radius_km=frac_radius_km,
            frac_window_days=frac_window_days,
            station_radius_km=station_radius_km,
            engine=engine,
            likely_driver=likely_driver,
            confidence=confidence,
            signals_json=signals_json,
            nearby_swd_count=nearby_swd_count,
            nearby_frac_count=nearby_frac_count,
            nearby_station_count=nearby_station_count,
            frac_data_quality=frac_data_quality,
            mc_frac_score_mean=mc_frac_score_mean,
            mc_frac_score_p5=mc_frac_score_p5,
            mc_frac_score_p95=mc_frac_score_p95,
            adjusted_likely_driver=adjusted_likely_driver,
            adjusted_confidence=adjusted_confidence,
        )
        self.db.add(snap)
        self.db.commit()
        self.db.refresh(snap)
        return snap

    def get_latest(self, event_id: str) -> Optional[EventContextSnapshot]:
        return (
            self.db.query(EventContextSnapshot)
            .filter(EventContextSnapshot.event_id == event_id)
            .order_by(EventContextSnapshot.run_timestamp.desc())
            .first()
        )

    def list_for_event(self, event_id: str) -> list[EventContextSnapshot]:
        return (
            self.db.query(EventContextSnapshot)
            .filter(EventContextSnapshot.event_id == event_id)
            .order_by(EventContextSnapshot.run_timestamp.desc())
            .all()
        )
