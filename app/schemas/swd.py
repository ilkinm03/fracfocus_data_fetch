from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class SWDFetchResult(BaseModel):
    status: str
    source: str
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    error: Optional[str] = None


class SWDWellOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uic_number: str
    oil_gas_code: Optional[str] = None
    district_code: Optional[str] = None
    lease_number: Optional[str] = None
    well_no_display: Optional[str] = None
    api_no: Optional[str] = None
    activated_flag: Optional[bool] = None
    uic_type_injection: Optional[int] = None
    permit_canceled_date: Optional[datetime] = None
    max_liq_inj_pressure: Optional[float] = None
    max_gas_inj_pressure: Optional[float] = None
    prod_casing_pkr_depth: Optional[float] = None
    top_inj_zone: Optional[float] = None
    bot_inj_zone: Optional[float] = None
    lease_name: Optional[str] = None
    operator_number: Optional[int] = None
    field_number: Optional[int] = None
    bbl_vol_inj: Optional[float] = None
    mcf_vol_inj: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    fetched_at: Optional[datetime] = None


class SWDWellListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SWDWellOut]


class SWDMonitorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uic_no: str
    report_date: Optional[datetime] = None
    inj_press_avg: Optional[float] = None
    inj_press_max: Optional[float] = None
    vol_liq: Optional[float] = None
    vol_gas: Optional[float] = None
    toz: Optional[float] = None
    boz: Optional[float] = None
    commercial: Optional[int] = None
    fetched_at: Optional[datetime] = None


class SWDMonitorListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SWDMonitorOut]
