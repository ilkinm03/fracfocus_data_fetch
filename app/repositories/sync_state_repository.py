import zipfile
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from sqlalchemy.orm import Session
from app.models.sync_state import SyncState, CsvFileState

log = logging.getLogger(__name__)


class SyncStateRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_url(self, url: str) -> Optional[SyncState]:
        return self.db.query(SyncState).filter(SyncState.zip_url == url).first()

    def upsert(self, url: str, etag: Optional[str], last_modified: Optional[str]) -> SyncState:
        state = self.get_by_url(url)
        if state:
            state.etag = etag
            state.last_modified = last_modified
        else:
            state = SyncState(zip_url=url, etag=etag, last_modified=last_modified)
            self.db.add(state)
        self.db.commit()
        self.db.refresh(state)
        return state

    def set_status(
        self,
        url: str,
        status: str,
        error: Optional[str] = None,
        sync_time: Optional[datetime] = None,
    ) -> None:
        state = self.get_by_url(url)
        if state is None:
            state = SyncState(zip_url=url)
            self.db.add(state)
        state.last_sync_status = status
        state.error_message = error
        if sync_time:
            state.last_sync_at = sync_time
        self.db.commit()


class CsvFileStateRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_all(self) -> list[CsvFileState]:
        return self.db.query(CsvFileState).all()

    def get_by_filename(self, filename: str) -> Optional[CsvFileState]:
        return self.db.query(CsvFileState).filter(CsvFileState.filename == filename).first()

    def get_changed_files(self, zip_infos: list[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
        """
        Returns ZipInfo entries whose size or date differs from what's stored in DB.
        New files (no DB record) are always included.
        """
        changed = []
        for info in zip_infos:
            basename = Path(info.filename).name
            existing = self.get_by_filename(basename)
            if existing is None:
                changed.append(info)
            elif (
                existing.file_size != info.file_size
                or existing.compress_size != info.compress_size
                or existing.last_modified_zip != str(info.date_time)
            ):
                changed.append(info)
        return changed

    def upsert_after_processing(
        self, info: zipfile.ZipInfo, row_count: int
    ) -> None:
        """Updates ZIP metadata and marks the file as successfully processed."""
        basename = Path(info.filename).name
        state = self.get_by_filename(basename)
        if state:
            state.file_size = info.file_size
            state.compress_size = info.compress_size
            state.last_modified_zip = str(info.date_time)
            state.last_processed_at = datetime.utcnow()
            state.row_count = row_count
        else:
            state = CsvFileState(
                filename=basename,
                file_size=info.file_size,
                compress_size=info.compress_size,
                last_modified_zip=str(info.date_time),
                last_processed_at=datetime.utcnow(),
                row_count=row_count,
            )
            self.db.add(state)
        self.db.commit()
