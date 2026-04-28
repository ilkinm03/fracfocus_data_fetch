import logging
from datetime import datetime, timezone
from typing import Any, Optional
import requests
from app.core.config import Settings

log = logging.getLogger(__name__)


class USGSService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def fetch_delaware_events(
        self,
        min_magnitude: Optional[float] = None,
        page_size: int = 5000,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Fetches earthquake events from the USGS FDSN Event API for the Delaware
        Basin bounding box. Paginates via 1-based offset until the server returns
        fewer features than requested.

        Returns (rows, page_count). Rows are normalised to SeismicEvent column
        names and tagged source="usgs".
        """
        if min_magnitude is None:
            min_magnitude = self.settings.USGS_MIN_MAGNITUDE

        all_rows: list[dict[str, Any]] = []
        page_count = 0
        offset = 1  # USGS FDSN offset is 1-based

        while True:
            features = self._query_page(min_magnitude, offset, page_size)
            page_count += 1
            for feat in features:
                normalized = self._normalize(feat)
                if normalized is not None:
                    all_rows.append(normalized)
            log.info(
                f"USGS page fetched: offset={offset} returned={len(features)}"
                f" cumulative={len(all_rows)}"
            )
            if len(features) < page_size:
                break
            offset += len(features)

        return all_rows, page_count

    def _query_page(
        self,
        min_magnitude: float,
        offset: int,
        page_size: int,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "format": "geojson",
            "minlatitude": self.settings.TEXNET_BBOX_MIN_LAT,
            "maxlatitude": self.settings.TEXNET_BBOX_MAX_LAT,
            "minlongitude": self.settings.TEXNET_BBOX_MIN_LON,
            "maxlongitude": self.settings.TEXNET_BBOX_MAX_LON,
            "starttime": self.settings.USGS_START_TIME,
            "minmagnitude": min_magnitude,
            "eventtype": "earthquake",
            "orderby": "time-asc",
            "limit": page_size,
            "offset": offset,
        }
        response = requests.get(
            self.settings.USGS_FDSN_URL,
            params=params,
            timeout=self.settings.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("features", [])

    def _normalize(self, feat: dict[str, Any]) -> Optional[dict[str, Any]]:
        event_id = feat.get("id")
        if not event_id:
            return None

        props = feat.get("properties") or {}
        coords = (feat.get("geometry") or {}).get("coordinates") or []

        # The eventtype param filters server-side, but guard client-side too.
        if props.get("type") != "earthquake":
            return None

        return {
            "source": "usgs",
            "event_id": str(event_id),
            "magnitude": _to_float(props.get("mag")),
            "mag_type": _to_str(props.get("magType")),
            "latitude": _to_float(coords[1]) if len(coords) > 1 else None,
            "longitude": _to_float(coords[0]) if len(coords) > 0 else None,
            "depth": _to_float(coords[2]) if len(coords) > 2 else None,
            "event_type": _to_str(props.get("type")),
            "event_date": _epoch_ms_to_dt(props.get("time")),
            "evaluation_status": _to_str(props.get("status")),
            "rms": _to_float(props.get("rms")),
            # USGS-specific starred fields
            "place": _to_str(props.get("place")),
            "title": _to_str(props.get("title")),
            "alternate_ids": _to_str(props.get("ids")),
            "gap": _to_float(props.get("gap")),
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


def _epoch_ms_to_dt(v: Any) -> Optional[datetime]:
    # USGS returns time as Unix epoch milliseconds, same as TexNet.
    if v is None or v == "":
        return None
    try:
        return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None
