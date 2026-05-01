from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends
from app.api.dependencies import get_sync_service, get_sync_history_repo
from app.repositories.sync_history_repository import SyncHistoryRepository
from app.schemas.fracfocus_sync import SyncStatusResponse, SyncTriggerResponse
from app.services.fracfocus_sync_service import SyncService

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/status", response_model=SyncStatusResponse)
def get_sync_status(sync_svc: SyncService = Depends(get_sync_service)):
    return sync_svc.get_status()


@router.post("/trigger", response_model=SyncTriggerResponse)
def trigger_sync(
    background_tasks: BackgroundTasks,
    sync_svc: SyncService = Depends(get_sync_service),
    history_repo: SyncHistoryRepository = Depends(get_sync_history_repo),
):
    if sync_svc.is_running():
        return SyncTriggerResponse(
            message="A sync is already in progress",
            triggered_at=datetime.utcnow(),
            status="already_running",
        )
    hist = history_repo.create("fracfocus", "pending")
    background_tasks.add_task(sync_svc.run_sync, hist.id)
    return SyncTriggerResponse(
        message="Sync started in background",
        triggered_at=datetime.utcnow(),
        status="started",
    )
