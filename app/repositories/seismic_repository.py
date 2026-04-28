from datetime import datetime
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.seismic_event import SeismicEvent


class SeismicEventRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def upsert_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        """
        Upserts events keyed on event_id. Returns (inserted, updated).
        Done one-by-one because SQLite's ON CONFLICT support varies and the
        catalog is small enough (low thousands) that bulk performance is fine.
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

    def count(self, county: Optional[str] = None, min_magnitude: Optional[float] = None) -> int:
        q = self.db.query(SeismicEvent)
        if county:
            q = q.filter(SeismicEvent.county_name == county.upper())
        if min_magnitude is not None:
            q = q.filter(SeismicEvent.magnitude >= min_magnitude)
        return q.count()

    def get_paginated(
        self,
        page: int,
        page_size: int,
        county: Optional[str] = None,
        min_magnitude: Optional[float] = None,
    ) -> list[SeismicEvent]:
        q = self.db.query(SeismicEvent)
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
