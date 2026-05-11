from datetime import datetime, timedelta
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.seismic_event import SeismicEvent


class SeismicEventRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def upsert_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        """
        Upserts events keyed on event_id. Returns (inserted, updated).
        Done row-by-row because the catalog is small enough (low thousands)
        and SQLite's ON CONFLICT handling across versions is inconsistent.
        """
        if not rows:
            return 0, 0

        now = datetime.utcnow()
        existing = {
            ev.event_id: ev
            for ev in self.db.query(SeismicEvent)
            .filter(SeismicEvent.event_id.in_([r["event_id"] for r in rows]))
            .all()
        }

        inserted = 0
        updated = 0
        for row in rows:
            current = existing.get(row["event_id"])
            if current is None:
                self.db.add(SeismicEvent(**row, fetched_at=now))
                inserted += 1
            else:
                for k, v in row.items():
                    setattr(current, k, v)
                current.fetched_at = now
                updated += 1
        self.db.commit()
        return inserted, updated

    def count(
        self,
        source: Optional[str] = None,
        county: Optional[str] = None,
        min_magnitude: Optional[float] = None,
    ) -> int:
        q = self.db.query(SeismicEvent)
        if source:
            q = q.filter(SeismicEvent.source == source.lower())
        if county:
            q = q.filter(SeismicEvent.county_name == county.upper())
        if min_magnitude is not None:
            q = q.filter(SeismicEvent.magnitude >= min_magnitude)
        return q.count()

    def get_by_event_id(self, event_id: str) -> Optional[SeismicEvent]:
        return (
            self.db.query(SeismicEvent)
            .filter(SeismicEvent.event_id == event_id)
            .first()
        )

    def get_paginated(
        self,
        page: int,
        page_size: int,
        source: Optional[str] = None,
        county: Optional[str] = None,
        min_magnitude: Optional[float] = None,
    ) -> list[SeismicEvent]:
        q = self.db.query(SeismicEvent)
        if source:
            q = q.filter(SeismicEvent.source == source.lower())
        if county:
            q = q.filter(SeismicEvent.county_name == county.upper())
        if min_magnitude is not None:
            q = q.filter(SeismicEvent.magnitude >= min_magnitude)
        return (
            q.order_by(SeismicEvent.event_date.desc().nullslast())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

    def find_nearby_events(
        self,
        lat: float,
        lon: float,
        radius_km: float,
        event_date: Optional[datetime],
        window_days: int = 365,
        min_magnitude: float = 1.5,
    ) -> list[SeismicEvent]:
        """Return events within a bounding box and time window for sequence statistics.

        Uses a bbox pre-filter (fast) then returns candidates sorted by event_date
        ascending. The caller can apply an exact Haversine filter if sub-km precision
        is required; for sequence statistics the bbox approximation is sufficient.
        """
        deg_per_km = 1.0 / 111.0
        pad = radius_km * deg_per_km
        q = (
            self.db.query(SeismicEvent)
            .filter(SeismicEvent.latitude  >= lat - pad)
            .filter(SeismicEvent.latitude  <= lat + pad)
            .filter(SeismicEvent.longitude >= lon - pad)
            .filter(SeismicEvent.longitude <= lon + pad)
        )
        if event_date is not None:
            q = q.filter(SeismicEvent.event_date >= event_date - timedelta(days=window_days))
            q = q.filter(SeismicEvent.event_date <= event_date)
        if min_magnitude is not None:
            q = q.filter(SeismicEvent.magnitude >= min_magnitude)
        return q.order_by(SeismicEvent.event_date.asc()).all()
