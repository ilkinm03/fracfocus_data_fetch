import json
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.dependencies import (
    get_event_context_service,
    get_event_context_repo,
    get_attribution_service,
)
from app.repositories.event_context_repository import EventContextRepository
from app.services.event_context_service import EventContextService
from app.services.physics_attribution_service import PhysicsAttributionService
from app.schemas.analysis import EventContextOut, EventAnalysisOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get(
    "/events/{event_id}/context",
    response_model=EventContextOut,
    summary="Assemble nearby SWD, frac, and station context for a seismic event",
    description=(
        "Spatial + temporal join of one seismic event with nearby SWD wells (incl. H-10 "
        "history), nearby FracFocus jobs, and nearby IRIS stations. Does not persist a "
        "snapshot — use POST /analyze for that. Returns 404 if event_id is unknown."
    ),
)
def get_event_context(
    event_id: str,
    swd_radius_km: float = Query(None, ge=0, le=200, description="SWD search radius (km)"),
    swd_window_days: int = Query(None, ge=1, le=36500, description="SWD lookback window (days)"),
    frac_radius_km: float = Query(None, ge=0, le=200, description="Frac search radius (km)"),
    frac_window_days: int = Query(None, ge=1, le=36500, description="Frac lookback window (days)"),
    station_radius_km: float = Query(None, ge=0, le=500, description="Station search radius (km)"),
    svc: EventContextService = Depends(get_event_context_service),
) -> EventContextOut:
    ctx = svc.assemble(
        event_id=event_id,
        swd_radius_km=swd_radius_km,
        swd_window_days=swd_window_days,
        frac_radius_km=frac_radius_km,
        frac_window_days=frac_window_days,
        station_radius_km=station_radius_km,
    )
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    return ctx


@router.post(
    "/events/{event_id}/analyze",
    response_model=EventAnalysisOut,
    summary="Run attribution analysis and persist snapshot",
    description=(
        "Assembles event context, runs the heuristic attribution engine (placeholder for "
        "the Permian physics engine), persists an event_context_snapshot row, and returns "
        "the full result. Each call appends a new snapshot row — prior runs are preserved. "
        "Returns 404 if event_id is unknown."
    ),
)
def analyze_event(
    event_id: str,
    swd_radius_km: float = Query(None, ge=0, le=200),
    swd_window_days: int = Query(None, ge=1, le=36500),
    frac_radius_km: float = Query(None, ge=0, le=200),
    frac_window_days: int = Query(None, ge=1, le=36500),
    station_radius_km: float = Query(None, ge=0, le=500),
    svc: EventContextService = Depends(get_event_context_service),
    ctx_repo: EventContextRepository = Depends(get_event_context_repo),
    attribution_svc: PhysicsAttributionService = Depends(get_attribution_service),
) -> EventAnalysisOut:
    ctx = svc.assemble(
        event_id=event_id,
        swd_radius_km=swd_radius_km,
        swd_window_days=swd_window_days,
        frac_radius_km=frac_radius_km,
        frac_window_days=frac_window_days,
        station_radius_km=station_radius_km,
    )
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")

    result = attribution_svc.score(ctx)

    signals_json = json.dumps([s.model_dump() for s in result.signals])

    snap = ctx_repo.save_snapshot(
        event_id=event_id,
        run_timestamp=datetime.utcnow(),
        swd_radius_km=ctx.swd_radius_km,
        swd_window_days=ctx.swd_window_days,
        frac_radius_km=ctx.frac_radius_km,
        frac_window_days=ctx.frac_window_days,
        station_radius_km=ctx.station_radius_km,
        engine=result.engine,
        likely_driver=result.likely_driver,
        confidence=result.confidence,
        signals_json=signals_json,
        nearby_swd_count=len(ctx.nearby_swd_wells),
        nearby_frac_count=len(ctx.nearby_frac_jobs),
        nearby_station_count=len(ctx.nearby_stations),
        frac_data_quality=result.frac_data_quality,
        mc_frac_score_mean=result.mc_frac_score_mean,
        mc_frac_score_p5=result.mc_frac_score_p5,
        mc_frac_score_p95=result.mc_frac_score_p95,
        adjusted_likely_driver=result.adjusted_likely_driver,
        adjusted_confidence=result.adjusted_confidence,
    )

    return EventAnalysisOut(
        snapshot_id=snap.id,
        context=ctx,
        attribution=result,
    )
