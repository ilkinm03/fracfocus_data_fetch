import logging
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
import requests
from app.core.config import Settings

log = logging.getLogger(__name__)

DELAWARE_COUNTIES = {"CULBERSON", "REEVES", "LOVING", "WARD", "WINKLER", "PECOS"}

# Starred PoC fields from the Delaware data plan. We request these explicitly
# so we don't accidentally rely on defaults if the layer schema grows.
OUT_FIELDS = ",".join([
    "EventId", "Magnitude", "MagType", "Latitude", "Longitude", "Depth",
    "PhaseCount", "EventType", "RegionName", "Event_Date", "EvaluationStatus",
    "CountyName", "RMS", "StationCount",
])


class TexNetService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def fetch_delaware_events(
        self,
        min_magnitude: Optional[float] = None,
        page_size: int = 2000,
    ) -> list[dict[str, Any]]:
        """
        Pulls every earthquake event in the Delaware Basin bounding box from the
        TexNet ArcGIS REST layer. Returns rows already normalized to the column
        names of the SeismicEvent model.

        Pagination follows the ArcGIS REST pattern: keep advancing resultOffset
        until the server stops setting exceededTransferLimit. Sorting by EventId
        gives a stable order so paged results don't overlap or skip.
        """
        all_rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            features, exceeded = self._query_page(min_magnitude, offset, page_size)
            for feat in features:
                normalized = self._normalize(feat.get("attributes") or {})
                if normalized is not None:
                    all_rows.append(normalized)
            log.info(
                f"TexNet page fetched: offset={offset} returned={len(features)}"
                f" cumulative={len(all_rows)} more={exceeded}"
            )
            if not exceeded or not features:
                break
            offset += len(features)
        return all_rows

    def _query_page(
        self, min_magnitude: Optional[float], offset: int, page_size: int
    ) -> tuple[list[dict[str, Any]], bool]:
        where_parts = ["EventType = 'earthquake'"]
        if min_magnitude is not None:
            where_parts.append(f"Magnitude >= {float(min_magnitude)}")

        params = {
            "where": " AND ".join(where_parts),
            "geometry": (
                f"{self.settings.TEXNET_BBOX_MIN_LON},{self.settings.TEXNET_BBOX_MIN_LAT},"
                f"{self.settings.TEXNET_BBOX_MAX_LON},{self.settings.TEXNET_BBOX_MAX_LAT}"
            ),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": OUT_FIELDS,
            "returnGeometry": "false",
            "orderByFields": "EventId ASC",
            "resultOffset": str(offset),
            "resultRecordCount": str(page_size),
            "f": "json",
        }
        url = self.settings.TEXNET_REST_URL.rstrip("/") + "/query"
        response = requests.get(url, params=params, timeout=self.settings.REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            raise RuntimeError(f"TexNet ArcGIS error: {payload['error']}")
        return payload.get("features", []), bool(payload.get("exceededTransferLimit"))

    def _normalize(self, attrs: dict[str, Any]) -> Optional[dict[str, Any]]:
        event_id = attrs.get("EventId")
        if not event_id:
            return None
        county = attrs.get("CountyName")
        if county and county.strip().upper() not in DELAWARE_COUNTIES:
            # Bbox can spill into adjacent counties — drop them so the curated
            # table stays Delaware-only per the trim rules.
            return None
        return {
            "source": "texnet",
            "event_id": str(event_id),
            "magnitude": _to_float(attrs.get("Magnitude")),
            "mag_type": _to_str(attrs.get("MagType")),
            "latitude": _to_float(attrs.get("Latitude")),
            "longitude": _to_float(attrs.get("Longitude")),
            "depth": _to_float(attrs.get("Depth")),
            "phase_count": _to_int(attrs.get("PhaseCount")),
            "event_type": _to_str(attrs.get("EventType")),
            "region_name": _to_str(attrs.get("RegionName")),
            "event_date": _epoch_ms_to_dt(attrs.get("Event_Date")),
            "evaluation_status": _to_str(attrs.get("EvaluationStatus")),
            "county_name": _to_str(county),
            "rms": _to_float(attrs.get("RMS")),
            "station_count": _to_int(attrs.get("StationCount")),
        }


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
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _epoch_ms_to_dt(v: Any) -> Optional[datetime]:
    # ArcGIS REST returns date fields as Unix epoch milliseconds.
    if v is None or v == "":
        return None
    try:
        return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None
