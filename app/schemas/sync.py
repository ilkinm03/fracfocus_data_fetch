from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class CsvFileStatus(BaseModel):
    filename: str
    last_processed_at: Optional[datetime] = None
    row_count: Optional[int] = None


class SyncStatusResponse(BaseModel):
    zip_url: str
    last_sync_at: Optional[datetime] = None
    last_sync_status: str
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    csv_files: list[CsvFileStatus] = []


class SyncTriggerResponse(BaseModel):
    message: str
    triggered_at: datetime
    status: str  # started | already_running


class SyncResult(BaseModel):
    status: str  # success | skipped | failed | already_running
    files_processed: int = 0
    total_rows_inserted: int = 0
    reason: Optional[str] = None
    error: Optional[str] = None
