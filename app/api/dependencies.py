from typing import Generator
from fastapi import Depends
from sqlalchemy.orm import Session
from app.core.config import Settings
from app.core.config import get_settings as _get_settings
from app.core.database import get_db as _get_db, engine
from app.repositories.fracfocus_repository import FracFocusRepository
from app.repositories.seismic_repository import SeismicEventRepository
from app.repositories.fracfocus_sync_state_repository import SyncStateRepository, CsvFileStateRepository
from app.services.fracfocus_download_service import DownloadService
from app.services.fracfocus_ingestion_service import CsvIngestionService
from app.services.fracfocus_sync_service import SyncService
from app.services.texnet_service import TexNetService
from app.services.usgs_service import USGSService


def get_db() -> Generator[Session, None, None]:
    yield from _get_db()


def get_settings() -> Settings:
    return _get_settings()


def get_download_service(
    settings: Settings = Depends(get_settings),
) -> DownloadService:
    return DownloadService(settings)


def get_fracfocus_repo() -> FracFocusRepository:
    return FracFocusRepository(engine)


def get_csv_file_state_repo(db: Session = Depends(get_db)) -> CsvFileStateRepository:
    return CsvFileStateRepository(db)


def get_texnet_service(
    settings: Settings = Depends(get_settings),
) -> TexNetService:
    return TexNetService(settings)


def get_usgs_service(
    settings: Settings = Depends(get_settings),
) -> USGSService:
    return USGSService(settings)


def get_seismic_repo(db: Session = Depends(get_db)) -> SeismicEventRepository:
    return SeismicEventRepository(db)


def get_sync_service(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    download_svc: DownloadService = Depends(get_download_service),
    fracfocus_repo: FracFocusRepository = Depends(get_fracfocus_repo),
    csv_file_state_repo: CsvFileStateRepository = Depends(get_csv_file_state_repo),
) -> SyncService:
    ingestion_svc = CsvIngestionService(fracfocus_repo, csv_file_state_repo)
    sync_state_repo = SyncStateRepository(db)
    return SyncService(
        db=db,
        download_svc=download_svc,
        ingestion_svc=ingestion_svc,
        sync_state_repo=sync_state_repo,
        csv_file_state_repo=csv_file_state_repo,
        settings=settings,
    )
