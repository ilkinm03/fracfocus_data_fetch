import logging
from datetime import datetime
from typing import Any, Optional
import requests
from app.core.config import Settings

log = logging.getLogger(__name__)

_DT_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S")


class IRISService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def fetch_delaware_stations(self) -> tuple[list[dict[str, Any]], int]:
        """
        Fetches seismic station metadata from EarthScope (IRIS) FDSN Station API
        for the Delaware Basin bounding box using the pipe-delimited text format.
        Returns (rows, page_count=1) — the station service returns all results in
        a single response with no pagination.
        """
        params: dict[str, Any] = {
            "format": "text",
            "level": "station",
            "minlatitude": self.settings.TEXNET_BBOX_MIN_LAT,
            "maxlatitude": self.settings.TEXNET_BBOX_MAX_LAT,
            "minlongitude": self.settings.TEXNET_BBOX_MIN_LON,
            "maxlongitude": self.settings.TEXNET_BBOX_MAX_LON,
        }
        response = requests.get(
            self.settings.IRIS_STATION_URL,
            params=params,
            timeout=self.settings.REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        raw: list[dict[str, Any]] = []
        for line in response.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            normalized = self._normalize(line)
            if normalized is not None:
                raw.append(normalized)

        # Multiple epoch rows can share the same network_station key (station reinstalled
        # or reconfigured). Keep the last occurrence so the upsert never hits a duplicate
        # key within the same batch.
        seen: dict[str, dict[str, Any]] = {}
        for r in raw:
            seen[r["network_station"]] = r
        rows = list(seen.values())

        log.info(
            f"IRIS stations fetched: {len(raw)} rows → {len(rows)} unique station(s)"
            " in Delaware Basin bbox"
        )
        return rows, 1

    def _normalize(self, line: str) -> Optional[dict[str, Any]]:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 6:
            return None

        network = parts[0] or None
        station_code = parts[1] or None
        if not network or not station_code:
            return None

        return {
            "network_station": f"{network}.{station_code}",
            "network": network,
            "station_code": station_code,
            "latitude": _to_float(parts[2]) if len(parts) > 2 else None,
            "longitude": _to_float(parts[3]) if len(parts) > 3 else None,
            "elevation": _to_float(parts[4]) if len(parts) > 4 else None,
            "site_name": _to_str(parts[5]) if len(parts) > 5 else None,
            "start_time": _parse_dt(parts[6]) if len(parts) > 6 else None,
            "end_time": _parse_dt(parts[7]) if len(parts) > 7 else None,
        }


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    s = str(v).strip()
    if not s:
        return None
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None
