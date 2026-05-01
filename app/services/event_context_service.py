import math
import logging
from datetime import datetime, timedelta
from typing import Optional

from app.core.config import Settings
from app.repositories.seismic_repository import SeismicEventRepository
from app.repositories.swd_repository import SWDRepository
from app.repositories.fracfocus_repository import FracFocusRepository
from app.repositories.iris_repository import IRISStationRepository
from app.schemas.analysis import (
    EventContextOut,
    NearbySWDWell,
    NearbyFracJob,
    NearbyStation,
)
from app.utils.geo import haversine_km

log = logging.getLogger(__name__)

# degrees-per-km approx for bounding-box padding (1 degree ≈ 111 km)
_DEG_PER_KM = 1.0 / 111.0


def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    pad = radius_km * _DEG_PER_KM
    return lat - pad, lat + pad, lon - pad, lon + pad


class EventContextService:
    def __init__(
        self,
        seismic_repo: SeismicEventRepository,
        swd_repo: SWDRepository,
        fracfocus_repo: FracFocusRepository,
        iris_repo: IRISStationRepository,
        settings: Settings,
    ) -> None:
        self.seismic_repo = seismic_repo
        self.swd_repo = swd_repo
        self.fracfocus_repo = fracfocus_repo
        self.iris_repo = iris_repo
        self.settings = settings

    def assemble(
        self,
        event_id: str,
        swd_radius_km: Optional[float] = None,
        swd_window_days: Optional[int] = None,
        frac_radius_km: Optional[float] = None,
        frac_window_days: Optional[int] = None,
        station_radius_km: Optional[float] = None,
    ) -> Optional[EventContextOut]:
        s = self.settings
        swd_r = swd_radius_km if swd_radius_km is not None else s.ANALYSIS_SWD_RADIUS_KM
        swd_w = swd_window_days if swd_window_days is not None else s.ANALYSIS_SWD_WINDOW_DAYS
        frac_r = frac_radius_km if frac_radius_km is not None else s.ANALYSIS_FRAC_RADIUS_KM
        frac_w = frac_window_days if frac_window_days is not None else s.ANALYSIS_FRAC_WINDOW_DAYS
        sta_r = station_radius_km if station_radius_km is not None else s.ANALYSIS_STATION_RADIUS_KM

        event = self.seismic_repo.get_by_event_id(event_id)
        if event is None:
            return None

        ev_lat: float = event.latitude or 0.0
        ev_lon: float = event.longitude or 0.0
        ev_date: Optional[datetime] = event.event_date

        nearby_swd = self._nearby_swd(ev_lat, ev_lon, ev_date, swd_r, swd_w)
        nearby_frac = self._nearby_frac(ev_lat, ev_lon, ev_date, frac_r, frac_w)
        nearby_stations = self._nearby_stations(ev_lat, ev_lon, sta_r)

        return EventContextOut(
            event_id=event_id,
            event_latitude=event.latitude,
            event_longitude=event.longitude,
            event_depth_km=event.depth,
            event_date=ev_date,
            event_magnitude=event.magnitude,
            swd_radius_km=swd_r,
            swd_window_days=swd_w,
            frac_radius_km=frac_r,
            frac_window_days=frac_w,
            station_radius_km=sta_r,
            nearby_swd_wells=nearby_swd,
            nearby_frac_jobs=nearby_frac,
            nearby_stations=nearby_stations,
        )

    # ------------------------------------------------------------------ SWD

    def _nearby_swd(
        self,
        ev_lat: float,
        ev_lon: float,
        ev_date: Optional[datetime],
        radius_km: float,
        window_days: int,
    ) -> list[NearbySWDWell]:
        min_lat, max_lat, min_lon, max_lon = _bbox(ev_lat, ev_lon, radius_km)
        candidates = self.swd_repo.find_wells_in_bbox(min_lat, max_lat, min_lon, max_lon)

        result: list[NearbySWDWell] = []
        for well in candidates:
            if well.latitude is None or well.longitude is None:
                continue
            d = haversine_km(ev_lat, ev_lon, well.latitude, well.longitude)
            if d > radius_km:
                continue

            monthly_count = 0
            cum_bbl = 0.0
            avg_psi: Optional[float] = None
            max_psi: Optional[float] = None

            if ev_date is not None:
                window_start = ev_date - timedelta(days=window_days)
                records = self.swd_repo.get_monitoring_window(
                    well.uic_number, window_start, ev_date
                )
                monthly_count = len(records)
                bbls = [r.vol_liq for r in records if r.vol_liq is not None]
                pressures_avg = [r.inj_press_avg for r in records if r.inj_press_avg is not None]
                pressures_max = [r.inj_press_max for r in records if r.inj_press_max is not None]
                cum_bbl = sum(bbls)
                avg_psi = sum(pressures_avg) / len(pressures_avg) if pressures_avg else None
                max_psi = max(pressures_max) if pressures_max else None

            result.append(
                NearbySWDWell(
                    uic_number=well.uic_number,
                    api_no=well.api_no,
                    distance_km=round(d, 3),
                    latitude=well.latitude,
                    longitude=well.longitude,
                    top_inj_zone=well.top_inj_zone,
                    bot_inj_zone=well.bot_inj_zone,
                    monthly_record_count=monthly_count,
                    cumulative_bbl=cum_bbl,
                    avg_pressure_psi=avg_psi,
                    max_pressure_psi=max_psi,
                )
            )

        result.sort(key=lambda w: w.distance_km)
        log.debug(f"SWD nearby: {len(result)} wells within {radius_km} km of event")
        return result

    # ------------------------------------------------------------------ Frac

    def _nearby_frac(
        self,
        ev_lat: float,
        ev_lon: float,
        ev_date: Optional[datetime],
        radius_km: float,
        window_days: int,
    ) -> list[NearbyFracJob]:
        if ev_date is None:
            return []

        min_lat, max_lat, min_lon, max_lon = _bbox(ev_lat, ev_lon, radius_km)
        window_start = ev_date - timedelta(days=window_days)

        rows = self.fracfocus_repo.find_nearby(
            min_lat, max_lat, min_lon, max_lon,
            start_date=window_start.date(),
            end_date=ev_date.date(),
        )

        result: list[NearbyFracJob] = []
        for row in rows:
            try:
                lat = float(row.get("latitude") or 0)
                lon = float(row.get("longitude") or 0)
            except (TypeError, ValueError):
                continue
            if lat == 0.0 and lon == 0.0:
                continue
            d = haversine_km(ev_lat, ev_lon, lat, lon)
            if d > radius_km:
                continue

            water_vol: Optional[float] = None
            raw_wv = row.get("totalbasewatervolume")
            if raw_wv not in (None, "", "None"):
                try:
                    water_vol = float(raw_wv)
                except (TypeError, ValueError):
                    pass

            form_depth: Optional[float] = None
            raw_fd = row.get("tvd")
            if raw_fd not in (None, "", "None"):
                try:
                    form_depth = float(raw_fd)
                except (TypeError, ValueError):
                    pass

            result.append(
                NearbyFracJob(
                    api_number=row.get("apinumber"),
                    distance_km=round(d, 3),
                    latitude=lat,
                    longitude=lon,
                    job_start_date=row.get("jobstartdate"),
                    job_end_date=row.get("jobenddate"),
                    operator_name=row.get("operatorname"),
                    well_name=row.get("wellname"),
                    total_water_volume=water_vol,
                    formation_depth=form_depth,
                )
            )

        result.sort(key=lambda j: j.distance_km)
        log.debug(f"Frac nearby: {len(result)} jobs within {radius_km} km of event")
        return result

    # --------------------------------------------------------------- Stations

    def _nearby_stations(
        self,
        ev_lat: float,
        ev_lon: float,
        radius_km: float,
    ) -> list[NearbyStation]:
        min_lat, max_lat, min_lon, max_lon = _bbox(ev_lat, ev_lon, radius_km)
        candidates = self.iris_repo.find_stations_in_bbox(min_lat, max_lat, min_lon, max_lon)

        result: list[NearbyStation] = []
        for sta in candidates:
            if sta.latitude is None or sta.longitude is None:
                continue
            d = haversine_km(ev_lat, ev_lon, sta.latitude, sta.longitude)
            if d > radius_km:
                continue
            result.append(
                NearbyStation(
                    network_station=sta.network_station,
                    network=sta.network,
                    station_code=sta.station_code,
                    distance_km=round(d, 3),
                    latitude=sta.latitude,
                    longitude=sta.longitude,
                    site_name=sta.site_name,
                    end_time=sta.end_time,
                )
            )

        result.sort(key=lambda s: s.distance_km)
        return result
