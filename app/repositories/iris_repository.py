from datetime import datetime
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.iris_station import IRISStation


class IRISStationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def upsert_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        """Upserts stations keyed on network_station. Returns (inserted, updated)."""
        if not rows:
            return 0, 0

        now = datetime.utcnow()
        keys = [r["network_station"] for r in rows]
        existing = {
            s.network_station: s
            for s in self.db.query(IRISStation)
            .filter(IRISStation.network_station.in_(keys))
            .all()
        }

        inserted = 0
        updated = 0
        for row in rows:
            current = existing.get(row["network_station"])
            if current is None:
                self.db.add(IRISStation(**row, fetched_at=now))
                inserted += 1
            else:
                for k, v in row.items():
                    setattr(current, k, v)
                current.fetched_at = now
                updated += 1
        self.db.commit()
        return inserted, updated

    def count(self, network: Optional[str] = None, active_only: bool = False) -> int:
        q = self.db.query(IRISStation)
        if network:
            q = q.filter(IRISStation.network == network.upper())
        if active_only:
            q = q.filter(IRISStation.end_time.is_(None))
        return q.count()

    def get_paginated(
        self,
        page: int,
        page_size: int,
        network: Optional[str] = None,
        active_only: bool = False,
    ) -> list[IRISStation]:
        q = self.db.query(IRISStation)
        if network:
            q = q.filter(IRISStation.network == network.upper())
        if active_only:
            q = q.filter(IRISStation.end_time.is_(None))
        return (
            q.order_by(IRISStation.network, IRISStation.station_code)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
