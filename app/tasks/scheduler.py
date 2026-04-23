import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from app.core.config import get_settings
from app.core.database import SessionLocal, engine, init_db

log = logging.getLogger(__name__)
scheduler = BackgroundScheduler()


def _run_scheduled_sync() -> None:
    """
    Runs inside an APScheduler thread — builds its own session and service graph
    because FastAPI's DI system is unavailable in this context.
    """
    from app.repositories.fracfocus_repository import FracFocusRepository
    from app.repositories.sync_state_repository import SyncStateRepository, CsvFileStateRepository
    from app.services.download_service import DownloadService
    from app.services.csv_ingestion_service import CsvIngestionService
    from app.services.sync_service import SyncService

    settings = get_settings()
    db = SessionLocal()
    try:
        fracfocus_repo = FracFocusRepository(engine)
        csv_file_state_repo = CsvFileStateRepository(db)
        ingestion_svc = CsvIngestionService(fracfocus_repo, csv_file_state_repo)
        sync_state_repo = SyncStateRepository(db)
        svc = SyncService(
            db=db,
            download_svc=DownloadService(settings),
            ingestion_svc=ingestion_svc,
            sync_state_repo=sync_state_repo,
            csv_file_state_repo=csv_file_state_repo,
            settings=settings,
        )
        result = svc.run_sync()
        log.info(f"Scheduled sync completed: {result}")
    except Exception:
        log.exception("Scheduled sync raised an unexpected error")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    init_db()

    settings = get_settings()
    if settings.SYNC_ENABLED:
        scheduler.add_job(
            func=_run_scheduled_sync,
            trigger=CronTrigger(day=settings.SYNC_CRON_DAY, hour=settings.SYNC_CRON_HOUR),
            id="monthly_sync",
            replace_existing=True,
        )
        scheduler.start()
        log.info(
            f"Scheduler started: monthly sync on day={settings.SYNC_CRON_DAY}"
            f" hour={settings.SYNC_CRON_HOUR}"
        )

    yield

    if scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")
