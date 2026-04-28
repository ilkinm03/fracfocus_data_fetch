import logging
from typing import Optional
from fastapi import APIRouter, Depends, Query
from app.api.dependencies import get_seismic_repo, get_texnet_service
from app.repositories.seismic_repository import SeismicEventRepository
from app.schemas.seismic import (
    SeismicEventListResponse,
    SeismicEventOut,
    TexNetFetchResult,
)
from app.services.texnet_service import TexNetService

log = logging.getLogger(__name__)
router = APIRouter(prefix="/seismic", tags=["seismic"])


@router.post("/texnet/fetch", response_model=TexNetFetchResult)
def fetch_texnet(
    min_magnitude: Optional[float] = Query(
        None, description="Drop events below this magnitude before persisting"
    ),
    texnet: TexNetService = Depends(get_texnet_service),
    repo: SeismicEventRepository = Depends(get_seismic_repo),
):
    try:
        rows = texnet.fetch_delaware_events(min_magnitude=min_magnitude)
    except Exception as exc:
        log.exception("TexNet fetch failed")
        return TexNetFetchResult(status="failed", error=str(exc))

    inserted, updated = repo.upsert_many(rows)
    return TexNetFetchResult(
        status="success",
        fetched=len(rows),
        inserted=inserted,
        updated=updated,
    )


@router.get("/events", response_model=SeismicEventListResponse)
def list_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    county: Optional[str] = Query(None, description="Delaware county name (case-insensitive)"),
    min_magnitude: Optional[float] = Query(None, description="Filter to events at or above this magnitude"),
    repo: SeismicEventRepository = Depends(get_seismic_repo),
):
    total = repo.count(county=county, min_magnitude=min_magnitude)
    items = repo.get_paginated(page, page_size, county=county, min_magnitude=min_magnitude)
    return SeismicEventListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[SeismicEventOut.model_validate(item) for item in items],
    )
