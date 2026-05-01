import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from app.models.sync_history import SyncHistory

log = logging.getLogger(__name__)


class SyncHistoryRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, source: str, status: str = "running") -> SyncHistory:
        now = datetime.utcnow()
        row = SyncHistory(
            source=source,
            status=status,
            created_at=now,
            started_at=now if status != "pending" else None,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        log.info(f"sync_history [{row.id}] {source} → {status}")
        return row

    def mark_running(self, history_id: int) -> None:
        row = self.db.get(SyncHistory, history_id)
        if row is None:
            return
        row.status = "running"
        row.started_at = datetime.utcnow()
        self.db.commit()
        log.info(f"sync_history [{history_id}] → running")

    def finish(
        self,
        history_id: int,
        status: str,
        rows_inserted: Optional[int] = None,
        rows_updated: Optional[int] = None,
        detail: Optional[str] = None,
    ) -> None:
        row = self.db.get(SyncHistory, history_id)
        if row is None:
            log.warning(f"sync_history [{history_id}] not found — cannot finish")
            return
        row.status = status
        row.finished_at = datetime.utcnow()
        if rows_inserted is not None:
            row.rows_inserted = rows_inserted
        if rows_updated is not None:
            row.rows_updated = rows_updated
        if detail is not None:
            row.detail = detail
        self.db.commit()
        log.info(
            f"sync_history [{history_id}] {row.source} → {status} "
            f"inserted={rows_inserted} updated={rows_updated}"
        )

    def get_all(
        self,
        source: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[SyncHistory]:
        q = self.db.query(SyncHistory)
        if source:
            q = q.filter(SyncHistory.source == source)
        if status:
            q = q.filter(SyncHistory.status == status)
        return q.order_by(SyncHistory.id.desc()).limit(limit).all()

    def count(self, source: Optional[str] = None, status: Optional[str] = None) -> int:
        q = self.db.query(SyncHistory)
        if source:
            q = q.filter(SyncHistory.source == source)
        if status:
            q = q.filter(SyncHistory.status == status)
        return q.count()
