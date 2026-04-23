from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends
from app.api.dependencies import get_sync_service
from app.schemas.sync import SyncStatusResponse, SyncTriggerResponse
from app.services.sync_service import SyncService

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/status", response_model=SyncStatusResponse)
def get_sync_status(sync_svc: SyncService = Depends(get_sync_service)):
    return sync_svc.get_status()


@router.post("/trigger", response_model=SyncTriggerResponse)
def trigger_sync(
    background_tasks: BackgroundTasks,
    sync_svc: SyncService = Depends(get_sync_service),
):
    if sync_svc.is_running():
        return SyncTriggerResponse(
            message="A sync is already in progress",
            triggered_at=datetime.utcnow(),
            status="already_running",
        )
    background_tasks.add_task(sync_svc.run_sync)
    return SyncTriggerResponse(
        message="Sync started in background",
        triggered_at=datetime.utcnow(),
        status="started",
    )
