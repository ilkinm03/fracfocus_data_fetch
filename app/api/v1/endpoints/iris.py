import logging
from typing import Optional
from fastapi import APIRouter, Depends, Query
from app.api.dependencies import get_iris_service, get_iris_repo
from app.repositories.iris_repository import IRISStationRepository
from app.schemas.iris import IRISFetchResult, IRISStationListResponse, IRISStationOut
from app.services.iris_service import IRISService

log = logging.getLogger(__name__)
router = APIRouter(prefix="/seismic", tags=["iris"])


@router.post("/iris/stations/fetch", response_model=IRISFetchResult)
def fetch_iris_stations(
    iris: IRISService = Depends(get_iris_service),
    repo: IRISStationRepository = Depends(get_iris_repo),
):
    try:
        rows, _ = iris.fetch_delaware_stations()
    except Exception as exc:
        log.exception("IRIS station fetch failed")
        return IRISFetchResult(status="failed", error=str(exc))

    inserted, updated = repo.upsert_many(rows)
    return IRISFetchResult(
        status="success",
        fetched=len(rows),
        inserted=inserted,
        updated=updated,
    )


@router.get("/iris/stations", response_model=IRISStationListResponse)
def list_iris_stations(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    network: Optional[str] = Query(
        None,
        description="Filter by seismic network code (e.g. TX, N4, IU). Case-insensitive.",
    ),
    active_only: bool = Query(
        False,
        description="When true, only return stations with no end_time (currently operational).",
    ),
    repo: IRISStationRepository = Depends(get_iris_repo),
):
    total = repo.count(network=network, active_only=active_only)
    items = repo.get_paginated(page, page_size, network=network, active_only=active_only)
    return IRISStationListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[IRISStationOut.model_validate(item) for item in items],
    )
