from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, Text
from app.core.database import Base


class SyncHistory(Base):
    """One row per fetch/sync run across all pipelines.
    Tracks the full lifecycle: pending → running → success / failed / skipped."""
    __tablename__ = "sync_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(Text, nullable=False, index=True)  # fracfocus | uic | h10 | texnet | usgs | iris
    status = Column(Text, nullable=False)               # pending | running | success | failed | skipped
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    rows_inserted = Column(Integer)
    rows_updated = Column(Integer)
    detail = Column(Text)  # skip reason or error message
    created_at = Column(DateTime, default=datetime.utcnow)
