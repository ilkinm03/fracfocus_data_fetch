import logging
from datetime import datetime
from typing import Any, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from app.core.config import Settings

_RETRY = Retry(total=5, backoff_factor=2, status_forcelist=[500, 502, 503, 504])


def _session() -> requests.Session:
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=_RETRY)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

log = logging.getLogger(__name__)

_PAGE_SIZE = 5000
_UIC_CHUNK = 500   # max UIC numbers per Socrata IN clause


class H10Service:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def fetch_for_wells(
        self,
        uic_numbers: list[str],
        start_from: int = 0,
        resume_page_offset: int = 0,
        on_page_done: Optional[Any] = None,
    ) -> None:
        """
        Fetches H-10 monitoring records chunk by chunk, page by page.
        start_from: chunk index to resume from.
        resume_page_offset: page offset within the starting chunk.
        on_page_done(chunk_start, next_page_offset, page_rows) called after
        each page so the caller can persist a page-level checkpoint.
        """
        if not uic_numbers:
            return

        total_fetched = 0
        for chunk_start in range(start_from, len(uic_numbers), _UIC_CHUNK):
            chunk = uic_numbers[chunk_start : chunk_start + _UIC_CHUNK]
            page_offset = resume_page_offset if chunk_start == start_from else 0
            chunk_fetched = self._fetch_chunk(
                chunk,
                chunk_start=chunk_start,
                start_page_offset=page_offset,
                on_page_done=on_page_done,
            )
            total_fetched += chunk_fetched
            log.info(
                f"H-10 chunk {chunk_start}–{chunk_start + len(chunk)} done: "
                f"chunk_rows={chunk_fetched} total_so_far={total_fetched}"
            )

    def _fetch_chunk(
        self,
        uic_numbers: list[str],
        chunk_start: int,
        start_page_offset: int = 0,
        on_page_done: Optional[Any] = None,
    ) -> int:
        headers = {}
        if self.settings.SOCRATA_APP_TOKEN:
            headers["X-App-Token"] = self.settings.SOCRATA_APP_TOKEN

        quoted = ", ".join(f"'{u}'" for u in uic_numbers)
        where = f"uic_no IN ({quoted})"
        offset = start_page_offset or 0
        total = 0

        while True:
            params = {
                "$limit": _PAGE_SIZE,
                "$offset": offset,
                "$where": where,
                "$order": "uic_no ASC, formatted_date ASC",
            }
            log.info(
                f"H-10 chunk={chunk_start} page_offset={offset} fetching..."
            )
            resp = _session().get(
                self.settings.RRC_H10_URL,
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

            offset += len(page)
            total += len(page_rows)
            log.info(
                f"H-10 chunk={chunk_start} page_offset={offset} "
                f"page_rows={len(page_rows)} chunk_total={total}"
            )

            if on_page_done:
                on_page_done(chunk_start, offset, page_rows)

            if len(page) < _PAGE_SIZE:
                break

        return total

    def _normalize(self, rec: dict[str, Any]) -> Optional[dict[str, Any]]:
        uic_no = _to_str(rec.get("uic_no"))
        report_date = _to_dt(rec.get("formatted_date"))
        if not uic_no or report_date is None:
            return None
        return {
            "uic_no": uic_no,
            "report_date": report_date,
            "inj_press_avg": _to_float(rec.get("inj_press_avg")),
            "inj_press_max": _to_float(rec.get("inj_press_max")),
            "vol_liq": _to_float(rec.get("vol_liq")),
            "vol_gas": _to_float(rec.get("vol_gas")),
            "toz": _to_float(rec.get("toz")),
            "boz": _to_float(rec.get("boz")),
            "commercial": _to_int(rec.get("commercial")),
            "most_recent_record": _to_bool(rec.get("most_recent_record")),
            "type_uic": _to_str(rec.get("type_uic")),
        }


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


def _to_dt(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", ""))
    except (TypeError, ValueError):
        return None
