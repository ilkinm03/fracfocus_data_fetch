from typing import Optional
from fastapi import APIRouter, Depends, Query
from app.api.dependencies import get_sync_history_repo
from app.repositories.sync_history_repository import SyncHistoryRepository
from app.schemas.sync_history import SyncHistoryListResponse, SyncHistoryOut

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get(
    "/history",
    response_model=SyncHistoryListResponse,
    summary="Sync / fetch run history",
    description=(
        "Returns a log of every sync and fetch run across all pipelines "
        "(fracfocus, uic, h10, texnet, usgs, iris). "
        "Newest runs appear first. Filter by source and/or status."
    ),
)
def get_sync_history(
    source: Optional[str] = Query(None, description="fracfocus | uic | h10 | texnet | usgs | iris"),
    status: Optional[str] = Query(None, description="pending | running | success | failed | skipped"),
    limit: int = Query(100, ge=1, le=1000),
    repo: SyncHistoryRepository = Depends(get_sync_history_repo),
):
    total = repo.count(source=source, status=status)
    items = repo.get_all(source=source, status=status, limit=limit)
    return SyncHistoryListResponse(
        total=total,
        limit=limit,
        items=[SyncHistoryOut.model_validate(r) for r in items],
    )
