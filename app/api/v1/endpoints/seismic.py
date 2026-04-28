import logging
from typing import Optional
from fastapi import APIRouter, Depends, Query
from app.api.dependencies import get_seismic_repo, get_texnet_service, get_usgs_service
from app.repositories.seismic_repository import SeismicEventRepository
from app.schemas.seismic import SeismicEventListResponse, SeismicEventOut, SeismicFetchResult
from app.services.texnet_service import TexNetService
from app.services.usgs_service import USGSService

log = logging.getLogger(__name__)
router = APIRouter(prefix="/seismic", tags=["seismic"])


@router.post("/texnet/fetch", response_model=SeismicFetchResult)
def fetch_texnet(
    min_magnitude: Optional[float] = Query(
        None, description="Only fetch events at or above this magnitude (server-side filter)"
    ),
    texnet: TexNetService = Depends(get_texnet_service),
    repo: SeismicEventRepository = Depends(get_seismic_repo),
):
    try:
        rows = texnet.fetch_delaware_events(min_magnitude=min_magnitude)
    except Exception as exc:
        log.exception("TexNet fetch failed")
        return SeismicFetchResult(status="failed", source="texnet", error=str(exc))

    inserted, updated = repo.upsert_many(rows)
    return SeismicFetchResult(
        status="success",
        source="texnet",
        fetched=len(rows),
        inserted=inserted,
        updated=updated,
    )


@router.post("/usgs/fetch", response_model=SeismicFetchResult)
def fetch_usgs(
    min_magnitude: Optional[float] = Query(
        None,
        description=(
            "Only fetch events at or above this magnitude. "
            "Defaults to USGS_MIN_MAGNITUDE from settings (1.5)."
        ),
    ),
    usgs: USGSService = Depends(get_usgs_service),
    repo: SeismicEventRepository = Depends(get_seismic_repo),
):
    try:
        rows, pages = usgs.fetch_delaware_events(min_magnitude=min_magnitude)
    except Exception as exc:
        log.exception("USGS fetch failed")
        return SeismicFetchResult(status="failed", source="usgs", error=str(exc))

    inserted, updated = repo.upsert_many(rows)
    return SeismicFetchResult(
        status="success",
        source="usgs",
        fetched=len(rows),
        inserted=inserted,
        updated=updated,
        pages=pages,
    )


@router.get("/events", response_model=SeismicEventListResponse)
def list_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    source: Optional[str] = Query(None, description="Filter by catalog source: texnet | usgs"),
    county: Optional[str] = Query(None, description="Delaware county name (case-insensitive)"),
    min_magnitude: Optional[float] = Query(None),
    repo: SeismicEventRepository = Depends(get_seismic_repo),
):
    total = repo.count(source=source, county=county, min_magnitude=min_magnitude)
    items = repo.get_paginated(
        page, page_size, source=source, county=county, min_magnitude=min_magnitude
    )
    return SeismicEventListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[SeismicEventOut.model_validate(item) for item in items],
    )
