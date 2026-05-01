import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from sqlalchemy.orm import Session
from app.core.config import Settings
from app.repositories.fracfocus_repository import FracFocusRepository
from app.repositories.fracfocus_sync_state_repository import SyncStateRepository, CsvFileStateRepository
from app.repositories.sync_history_repository import SyncHistoryRepository
from app.services.fracfocus_download_service import DownloadService
from app.services.fracfocus_ingestion_service import CsvIngestionService
from app.schemas.fracfocus_sync import SyncResult, SyncStatusResponse, CsvFileStatus

log = logging.getLogger(__name__)

_sync_lock = threading.Lock()
_is_running = False


class SyncService:
    def __init__(
        self,
        db: Session,
        download_svc: DownloadService,
        ingestion_svc: CsvIngestionService,
        sync_state_repo: SyncStateRepository,
        csv_file_state_repo: CsvFileStateRepository,
        history_repo: SyncHistoryRepository,
        settings: Settings,
    ) -> None:
        self.db = db
        self.download_svc = download_svc
        self.ingestion_svc = ingestion_svc
        self.sync_state_repo = sync_state_repo
        self.csv_file_state_repo = csv_file_state_repo
        self.history_repo = history_repo
        self.settings = settings

    def is_running(self) -> bool:
        return _is_running

    def run_sync(self, history_id: Optional[int] = None) -> SyncResult:
        global _is_running
        with _sync_lock:
            if _is_running:
                return SyncResult(status="already_running", reason="Sync already in progress")
            _is_running = True
        if history_id is not None:
            self.history_repo.mark_running(history_id)
        try:
            return self._do_sync(history_id=history_id)
        finally:
            _is_running = False

    def _do_sync(self, history_id: Optional[int] = None) -> SyncResult:
        url = self.settings.ZIP_URL
        self.sync_state_repo.set_status(url, "running")

        try:
            # 1. Check whether the remote file has changed via HEAD request
            state = self.sync_state_repo.get_by_url(url)
            changed, new_etag, new_last_modified = self.download_svc.check_remote_changed(
                url,
                known_etag=state.etag if state else None,
                known_last_modified=state.last_modified if state else None,
            )

            if not changed:
                self.sync_state_repo.set_status(url, "success", sync_time=datetime.utcnow())
                if history_id is not None:
                    self.history_repo.finish(
                        history_id, "skipped",
                        detail="ETag and Last-Modified unchanged — no new data",
                    )
                return SyncResult(
                    status="skipped",
                    reason="ETag and Last-Modified unchanged — no new data",
                )

            # 2. Stream ZIP to disk
            zip_dest = Path(self.settings.EXTRACT_DIR).parent / "fracfocus_latest.zip"
            self.download_svc.stream_download_to_disk(url, zip_dest)

            # 3. Read ZIP central directory (metadata only, no decompression)
            csv_infos = self.download_svc.read_zip_csv_infos(zip_dest)
            log.info(f"ZIP contains {len(csv_infos)} CSV file(s)")

            # 4. Determine which CSV files have actually changed
            changed_infos = self.csv_file_state_repo.get_changed_files(csv_infos)
            log.info(f"{len(changed_infos)} CSV file(s) to process")

            if not changed_infos:
                self.sync_state_repo.upsert(url, new_etag, new_last_modified)
                self.sync_state_repo.set_status(url, "success", sync_time=datetime.utcnow())
                if history_id is not None:
                    self.history_repo.finish(
                        history_id, "skipped",
                        detail="ZIP changed but all CSV files are identical",
                    )
                return SyncResult(
                    status="skipped",
                    reason="ZIP changed but all CSV files are identical",
                )

            # 5. Extract only changed CSV files
            extract_dir = Path(self.settings.EXTRACT_DIR)
            extracted_paths = self.download_svc.extract_files(
                zip_dest,
                [info.filename for info in changed_infos],
                extract_dir,
            )

            # 6. Process each changed CSV and record state only on success
            total_rows = 0
            for csv_path, zip_info in zip(extracted_paths, changed_infos):
                row_count = self.ingestion_svc.process_csv(csv_path)
                self.csv_file_state_repo.upsert_after_processing(zip_info, row_count)
                total_rows += row_count

            # 7. Persist new ETag / Last-Modified and mark sync successful
            self.sync_state_repo.upsert(url, new_etag, new_last_modified)
            self.sync_state_repo.set_status(url, "success", sync_time=datetime.utcnow())
            if history_id is not None:
                self.history_repo.finish(
                    history_id, "success",
                    rows_inserted=total_rows,
                    detail=f"files_processed={len(changed_infos)}",
                )

            return SyncResult(
                status="success",
                files_processed=len(changed_infos),
                total_rows_inserted=total_rows,
            )

        except Exception as exc:
            log.exception("Sync failed")
            self.sync_state_repo.set_status(url, "failed", error=str(exc))
            if history_id is not None:
                self.history_repo.finish(history_id, "failed", detail=str(exc))
            return SyncResult(status="failed", error=str(exc))

    def get_status(self) -> SyncStatusResponse:
        url = self.settings.ZIP_URL
        state = self.sync_state_repo.get_by_url(url)
        csv_files = self.csv_file_state_repo.get_all()

        return SyncStatusResponse(
            zip_url=url,
            last_sync_at=state.last_sync_at if state else None,
            last_sync_status=state.last_sync_status if state else "never",
            etag=state.etag if state else None,
            last_modified=state.last_modified if state else None,
            csv_files=[
                CsvFileStatus(
                    filename=f.filename,
                    last_processed_at=f.last_processed_at,
                    row_count=f.row_count,
                )
                for f in csv_files
            ],
        )
