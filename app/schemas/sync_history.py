from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class SyncHistoryOut(BaseModel):
    id: int
    source: str
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    rows_inserted: Optional[int] = None
    rows_updated: Optional[int] = None
    detail: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class SyncHistoryListResponse(BaseModel):
    total: int
    limit: int
    items: list[SyncHistoryOut]
