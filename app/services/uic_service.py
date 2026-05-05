import logging
from datetime import datetime
from typing import Any, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from app.core.config import Settings

log = logging.getLogger(__name__)

_PAGE_SIZE = 5000
_RETRY = Retry(
    total=8,
    connect=5,
    read=3,
    backoff_factor=3,
    status_forcelist=[500, 502, 503, 504],
    raise_on_status=False,
)


def _session() -> requests.Session:
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=_RETRY)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


class UICService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def fetch_delaware_wells(
        self,
        start_offset: int = 0,
        on_page_done: Optional[Any] = None,
    ) -> list[dict[str, Any]]:
        """
        Fetches UIC well inventory page by page. start_offset resumes a previous
        run. on_page_done(next_offset, page_rows) called after each successful
        page so the caller can persist a checkpoint.
        """
        where = (
            f"latitude_nad83 >= {self.settings.TEXNET_BBOX_MIN_LAT}"
            f" AND latitude_nad83 <= {self.settings.TEXNET_BBOX_MAX_LAT}"
            f" AND longitude_nad83 >= {self.settings.TEXNET_BBOX_MIN_LON}"
            f" AND longitude_nad83 <= {self.settings.TEXNET_BBOX_MAX_LON}"
        )

        headers = {}
        if self.settings.SOCRATA_APP_TOKEN:
            headers["X-App-Token"] = self.settings.SOCRATA_APP_TOKEN

        all_rows: list[dict[str, Any]] = []
        offset = start_offset

        while True:
            params = {
                "$limit": _PAGE_SIZE,
                "$offset": offset,
                "$where": where,
                "$order": "uic_number ASC",
            }
            resp = _session().get(
                self.settings.RRC_UIC_URL,
                params=params,
                headers=headers,
                timeout=self.settings.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            page: list[dict] = resp.json()

            if not page:
                break

            page_rows = []
            for rec in page:
                normalized = self._normalize(rec)
                if normalized is not None:
                    page_rows.append(normalized)

            all_rows.extend(page_rows)
            offset += len(page)
            log.info(f"UIC page: offset={offset} returned={len(page)} total={len(all_rows)}")

            if on_page_done:
                on_page_done(offset, page_rows)

            if len(page) < _PAGE_SIZE:
                break

        return all_rows

    def _normalize(self, rec: dict[str, Any]) -> Optional[dict[str, Any]]:
        uic_number = _to_str(rec.get("uic_number"))
        if not uic_number:
            return None
        return {
            "uic_number": uic_number,
            "oil_gas_code": _to_str(rec.get("oil_gas_code")),
            "district_code": _to_str(rec.get("district_code")),
            "lease_number": _to_str(rec.get("lease_number")),
            "well_no_display": _to_str(rec.get("well_no_display")),
            "api_no": _to_str(rec.get("api_no")),
            "activated_flag": _to_bool(rec.get("activated_flag")),
            "uic_type_injection": _to_int(rec.get("uic_type_injection")),
            "permit_canceled_date": _to_dt(rec.get("permit_canceled_date")),
            "max_liq_inj_pressure": _to_float(rec.get("max_liq_inj_pressure")),
            "max_gas_inj_pressure": _to_float(rec.get("max_gas_inj_pressure")),
            "prod_casing_pkr_depth": _to_float(rec.get("prod_casing_pkr_depth")),
            "top_inj_zone": _to_float(rec.get("top_inj_zone")),
            "bot_inj_zone": _to_float(rec.get("bot_inj_zone")),
            "lease_name": _to_str(rec.get("lease_name")),
            "operator_number": _to_int(rec.get("operator_number")),
            "field_number": _to_int(rec.get("field_number")),
            "bbl_vol_inj": _to_float(rec.get("bbl_vol_inj")),
            "mcf_vol_inj": _to_float(rec.get("mcf_vol_inj")),
            "w14_date": _to_dt(rec.get("w14_date")),
            "w14_number": _to_str(rec.get("w14_number")),
            "letter_date": _to_dt(rec.get("letter_date")),
            "latitude": _to_float(rec.get("latitude_nad83")),
            "longitude": _to_float(rec.get("longitude_nad83")),
        }


def _to_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_bool(v: Any) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


def _to_dt(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    try:
        # Socrata returns ISO 8601 strings like "2021-03-15T00:00:00.000"
        return datetime.fromisoformat(str(v).replace("Z", ""))
    except (TypeError, ValueError):
        return None
