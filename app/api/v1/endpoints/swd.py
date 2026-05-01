import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.dependencies import get_swd_repo, get_uic_service, get_h10_service, get_sync_history_repo
from app.repositories.swd_repository import SWDRepository
from app.repositories.sync_history_repository import SyncHistoryRepository
from app.schemas.swd import (
    SWDFetchResult,
    SWDWellListResponse,
    SWDWellOut,
    SWDMonitorListResponse,
    SWDMonitorOut,
)
from app.services.uic_service import UICService
from app.services.h10_service import H10Service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/swd", tags=["swd"])


@router.post(
    "/uic/fetch",
    response_model=SWDFetchResult,
    summary="Fetch UIC well inventory from Texas Open Data Portal",
    description=(
        "Downloads UIC injection well inventory for the Delaware Basin bounding box "
        "from data.texas.gov (Socrata) and upserts into swd_wells page by page. "
        "Resumes automatically from last checkpoint if interrupted. "
        "Safe to re-run — upsert is keyed on uic_number."
    ),
)
def fetch_uic(
    uic_svc: UICService = Depends(get_uic_service),
    repo: SWDRepository = Depends(get_swd_repo),
    history_repo: SyncHistoryRepository = Depends(get_sync_history_repo),
):
    hist = history_repo.create("uic", "running")

    cp = repo.get_checkpoint("uic")
    start_offset = cp.progress_value if cp else 0
    inserted = cp.inserted_so_far if cp else 0
    updated = cp.updated_so_far if cp else 0

    if cp:
        log.info(f"UIC resuming from offset {start_offset} (checkpoint found)")

    def on_page_done(next_offset: int, page_rows: list) -> None:
        nonlocal inserted, updated
        ins, upd = repo.upsert_wells(page_rows)
        inserted += ins
        updated += upd
        repo.save_checkpoint(
            source="uic",
            progress_value=next_offset,
            total_count=0,
            inserted_so_far=inserted,
            updated_so_far=updated,
        )

    try:
        uic_svc.fetch_delaware_wells(
            start_offset=start_offset,
            on_page_done=on_page_done,
        )
    except Exception as exc:
        log.exception("UIC fetch failed — progress saved to checkpoint")
        history_repo.finish(hist.id, "failed", detail=str(exc))
        raise HTTPException(status_code=502, detail=str(exc))

    repo.clear_checkpoint("uic")
    history_repo.finish(hist.id, "success", rows_inserted=inserted, rows_updated=updated)
    return SWDFetchResult(
        status="success",
        source="uic",
        fetched=inserted + updated,
        inserted=inserted,
        updated=updated,
    )


@router.post(
    "/h10/fetch",
    response_model=SWDFetchResult,
    summary="Fetch H-10 monthly injection monitoring from Texas Open Data Portal",
    description=(
        "Fetches H-10 monthly injection records for all UIC wells in swd_wells. "
        "Run /uic/fetch first. Queries Socrata in 500-well chunks. "
        "Resumes automatically from last checkpoint if interrupted. "
        "Safe to re-run — upsert keyed on (uic_no, report_date)."
    ),
)
def fetch_h10(
    h10_svc: H10Service = Depends(get_h10_service),
    repo: SWDRepository = Depends(get_swd_repo),
    history_repo: SyncHistoryRepository = Depends(get_sync_history_repo),
):
    uic_numbers = repo.get_all_uic_numbers()
    if not uic_numbers:
        raise HTTPException(
            status_code=400,
            detail="No UIC wells in database — run POST /swd/uic/fetch first.",
        )

    hist = history_repo.create("h10", "running")

    cp = repo.get_checkpoint("h10")
    start_from = 0
    resume_page_offset = 0
    inserted = updated = 0

    if cp and cp.total_count == len(uic_numbers):
        start_from = cp.progress_value or 0
        resume_page_offset = cp.secondary_value or 0
        inserted = cp.inserted_so_far
        updated = cp.updated_so_far
        log.info(
            f"H-10 resuming from chunk={start_from} page_offset={resume_page_offset}"
        )
    else:
        repo.clear_checkpoint("h10")

    def on_page_done(chunk_start: int, next_page_offset: int, page_rows: list) -> None:
        nonlocal inserted, updated
        ins, upd = repo.upsert_monitoring(page_rows)
        inserted += ins
        updated += upd
        repo.save_checkpoint(
            source="h10",
            progress_value=chunk_start,
            secondary_value=next_page_offset,
            total_count=len(uic_numbers),
            inserted_so_far=inserted,
            updated_so_far=updated,
        )

    try:
        h10_svc.fetch_for_wells(
            uic_numbers,
            start_from=start_from,
            resume_page_offset=resume_page_offset,
            on_page_done=on_page_done,
        )
    except Exception as exc:
        log.exception(
            f"H-10 fetch failed at chunk={start_from} — progress saved to checkpoint"
        )
        history_repo.finish(hist.id, "failed", detail=str(exc))
        raise HTTPException(status_code=502, detail=str(exc))

    repo.clear_checkpoint("h10")
    history_repo.finish(hist.id, "success", rows_inserted=inserted, rows_updated=updated)
    return SWDFetchResult(
        status="success",
        source="h10",
        fetched=inserted + updated,
        inserted=inserted,
        updated=updated,
    )


@router.get(
    "/wells",
    response_model=SWDWellListResponse,
    summary="List UIC injection wells (Delaware Basin)",
)
def list_wells(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    repo: SWDRepository = Depends(get_swd_repo),
):
    total = repo.count_wells()
    items = repo.get_wells_paginated(page, page_size)
    return SWDWellListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[SWDWellOut.model_validate(w) for w in items],
    )


@router.get(
    "/monitoring",
    response_model=SWDMonitorListResponse,
    summary="List H-10 monthly injection records",
)
def list_monitoring(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    uic_no: Optional[str] = Query(None, description="Filter by UIC control number"),
    repo: SWDRepository = Depends(get_swd_repo),
):
    total = repo.count_monitoring(uic_no=uic_no)
    items = repo.get_monitoring_paginated(page, page_size, uic_no=uic_no)
    return SWDMonitorListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[SWDMonitorOut.model_validate(m) for m in items],
    )
