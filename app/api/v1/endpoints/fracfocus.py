from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.dependencies import get_fracfocus_repo
from app.repositories.fracfocus_repository import FracFocusRepository
from app.schemas.fracfocus import FracFocusListResponse

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/", response_model=FracFocusListResponse)
def list_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    state: Optional[str] = Query(None, description="Filter by state_name (exact match)"),
    operator: Optional[str] = Query(None, description="Filter by operator_name (partial match)"),
    repo: FracFocusRepository = Depends(get_fracfocus_repo),
):
    total, items = repo.get_paginated(page, page_size, state, operator)
    return FracFocusListResponse(total=total, page=page, page_size=page_size, items=items)


@router.get("/stats")
def get_stats(repo: FracFocusRepository = Depends(get_fracfocus_repo)):
    return {"total_records": repo.count()}


@router.get("/columns")
def list_columns(repo: FracFocusRepository = Depends(get_fracfocus_repo)):
    """Returns all column names in the fracfocus table."""
    return {"columns": repo.get_table_columns()}


@router.get("/distinct/{column}")
def distinct_values(
    column: str,
    repo: FracFocusRepository = Depends(get_fracfocus_repo),
):
    """
    Returns all distinct non-empty values for the given column.
    Example: /data/distinct/countyname
    """
    _validate_column(column, repo)
    values = repo.get_distinct_values(column)
    return {"column": column, "count": len(values), "values": values}


@router.get("/group/{column}")
def grouped_counts(
    column: str,
    repo: FracFocusRepository = Depends(get_fracfocus_repo),
):
    """
    Returns each distinct value with its row count, sorted by count descending.
    Example: /data/group/countyname  or  /data/group/statename
    """
    _validate_column(column, repo)
    results = repo.get_grouped_counts(column)
    return {"column": column, "groups": results}


def _validate_column(column: str, repo: FracFocusRepository) -> None:
    """Prevents SQL injection by checking the column exists in the actual table."""
    valid = repo.get_table_columns()
    if column not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"Column '{column}' not found. Available columns: {valid}",
        )
