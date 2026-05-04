import math
import logging
from app.schemas.analysis import AttributionResult, AttributionSignal, EventContextOut

log = logging.getLogger(__name__)

_ENGINE = "heuristic_v4"

# Spatial decay length-scales (km).
_SWD_LAMBDA_KM = 10.0    # Smye 2024: pressure fronts migrate ~10 km/yr in Delaware Basin
_FRAC_LAMBDA_KM = 3.0    # Poroelastic stress changes decay sharply with distance

# Temporal decay for SWD: pressure dissipates after injection stops.
# Half-life ≈ 8 months; λ = half_life / ln(2) ≈ 365 days.
_SWD_TIME_LAMBDA_DAYS = 365.0

# Depth mismatch penalty (Gaussian).
# σ = 3 km: at Δdepth=3 km weight≈0.61, at 6 km≈0.14, at 9 km≈0.01.
_DEPTH_SIGMA_KM = 3.0
_FT_TO_KM = 0.0003048    # RRC and FracFocus depths are in feet; seismic depth is in km

_GAL_PER_BBL = 42.0      # FracFocus totalbasewatervolume is in gallons; convert to bbl for unit parity

# Rate-change boost: multiply SWD score by the rate-change ratio, capped to prevent
# a single spike month from dominating the total.
_RATE_BOOST_CAP = 3.0


class HeuristicAttributionService:
    """Heuristic attribution engine. All λ / σ parameters are injectable so the
    calibration script can sweep them without modifying module constants. The module-
    level constants are the production defaults used when no overrides are passed.

    Replace with the Permian physics engine by swapping the dependency in
    app/api/dependencies.py — the response contract (AttributionResult) stays the same.
    """

    def __init__(
        self,
        swd_lambda_km: float = _SWD_LAMBDA_KM,
        frac_lambda_km: float = _FRAC_LAMBDA_KM,
        time_lambda_days: float = _SWD_TIME_LAMBDA_DAYS,
        depth_sigma_km: float = _DEPTH_SIGMA_KM,
        rate_boost_cap: float = _RATE_BOOST_CAP,
    ) -> None:
        self.swd_lambda_km = swd_lambda_km
        self.frac_lambda_km = frac_lambda_km
        self.time_lambda_days = time_lambda_days
        self.depth_sigma_km = depth_sigma_km
        self.rate_boost_cap = rate_boost_cap

    def score(self, context: EventContextOut) -> AttributionResult:
        swd_score = self._swd_score(context)
        frac_score = self._frac_score(context)

        driver, confidence = self._verdict(swd_score, frac_score)

        signals: list[AttributionSignal] = []
        for w in context.nearby_swd_wells:
            if w.cumulative_bbl > 0:
                spatial_w = math.exp(-w.distance_km / self.swd_lambda_km)
                temporal_w, days_since = self._temporal_weight(context, w, self.time_lambda_days)
                depth_w, delta_km = self._depth_weight(context.event_depth_km, w.top_inj_zone, w.bot_inj_zone, self.depth_sigma_km)
                rate_w = self._rate_boost(w, self.rate_boost_cap)
                weighted_val = w.cumulative_bbl * spatial_w * temporal_w * depth_w * rate_w
                recency_note = f", last report {days_since}d before event" if days_since is not None else ", recency unknown"
                depth_note = f", depth Δ{delta_km} km" if delta_km is not None else ""
                rate_note = f", rate ×{w.rate_change_ratio:.2f} (capped ×{rate_w:.2f})" if w.rate_change_ratio is not None else ""
                signals.append(
                    AttributionSignal(
                        name=f"SWD {w.uic_number}",
                        value=round(weighted_val, 2),
                        unit="weighted_bbl",
                        description=(
                            f"{w.uic_number} — {w.distance_km} km away, "
                            f"{w.cumulative_bbl:,.0f} bbl cumulative in window"
                            f"{recency_note}{depth_note}{rate_note}"
                        ),
                    )
                )
        for j in context.nearby_frac_jobs:
            wv_bbl = (j.total_water_volume or 0.0) / _GAL_PER_BBL
            if wv_bbl > 0:
                spatial_w = math.exp(-j.distance_km / self.frac_lambda_km)
                depth_w, delta_km = self._depth_weight(context.event_depth_km, j.formation_depth, j.formation_depth, self.depth_sigma_km)
                weighted_val = wv_bbl * spatial_w * depth_w
                depth_note = f", depth Δ{delta_km} km" if delta_km is not None else ""
                signals.append(
                    AttributionSignal(
                        name=f"FRAC {j.api_number or j.well_name or 'unknown'}",
                        value=round(weighted_val, 2),
                        unit="weighted_bbl",
                        description=(
                            f"Frac job at {j.distance_km} km, started {j.job_start_date}, "
                            f"{j.total_water_volume:,.0f} gal ({wv_bbl:,.0f} bbl) water volume"
                            f"{depth_note}"
                        ),
                    )
                )

        # Sort by value descending so the strongest signals appear first
        signals.sort(key=lambda s: s.value, reverse=True)

        log.info(
            f"Attribution [{_ENGINE}] event={context.event_id}: "
            f"driver={driver} confidence={confidence} "
            f"swd_score={swd_score:.2f} frac_score={frac_score:.2f}"
        )
        return AttributionResult(
            engine=_ENGINE,
            likely_driver=driver,
            confidence=confidence,
            swd_score=round(swd_score, 4),
            frac_score=round(frac_score, 4),
            signals=signals,
        )

    @staticmethod
    def _verdict(swd_score: float, frac_score: float) -> tuple[str, float]:
        """Return (driver, confidence) using a log-odds / softmax formulation.

        confidence = p_swd   when driver is "swd"   (range 0.5–1.0)
        confidence = 1−p_swd when driver is "frac"  (range 0.5–1.0)
        confidence = 0.0     when both scores are zero (no evidence)

        Unlike the old (winner−loser)/winner ratio, this value is directly
        interpretable as the probability that the identified driver is correct.
        Equal scores → p_swd = 0.5 → "indeterminate"; one side dominates →
        confidence approaches 1.0.
        """
        total = swd_score + frac_score
        if total == 0.0:
            return "indeterminate", 0.0
        p_swd = swd_score / total          # softmax; equivalent to sigmoid(log-odds)
        if p_swd > 0.5:
            return "swd", round(p_swd, 4)
        if p_swd < 0.5:
            return "frac", round(1.0 - p_swd, 4)
        return "indeterminate", 0.5        # exact tie, non-zero evidence

    @staticmethod
    def _temporal_weight(context: EventContextOut, w, time_lambda_days: float) -> tuple[float, int | None]:
        """Returns (temporal_weight, days_since_last_report).
        If last_report_date or event_date is missing, weight defaults to 1.0 (no penalty)."""
        if context.event_date is None or w.last_report_date is None:
            return 1.0, None
        days_since = max((context.event_date - w.last_report_date).days, 0)
        return math.exp(-days_since / time_lambda_days), days_since

    @staticmethod
    def _depth_weight(event_depth_km: float | None, zone_top_ft: float | None, zone_bot_ft: float | None, depth_sigma_km: float) -> tuple[float, float | None]:
        """Gaussian penalty for depth mismatch between event and injection zone.
        Returns (weight, delta_km). Both depths must be present; zone depths are in feet."""
        if event_depth_km is None or zone_top_ft is None or zone_bot_ft is None:
            return 1.0, None
        mid_km = ((zone_top_ft + zone_bot_ft) / 2.0) * _FT_TO_KM
        delta_km = abs(event_depth_km - mid_km)
        weight = math.exp(-(delta_km ** 2) / (2.0 * depth_sigma_km ** 2))
        return weight, round(delta_km, 2)

    @staticmethod
    def _rate_boost(w, rate_boost_cap: float) -> float:
        """Multiplicative boost from recent injection rate acceleration, capped at rate_boost_cap.
        Returns 1.0 (neutral) when rate_change_ratio is unavailable."""
        if w.rate_change_ratio is None:
            return 1.0
        return min(w.rate_change_ratio, rate_boost_cap)

    def _swd_score(self, context: EventContextOut) -> float:
        total = 0.0
        for w in context.nearby_swd_wells:
            if w.cumulative_bbl > 0 and w.distance_km > 0:
                spatial_w = math.exp(-w.distance_km / self.swd_lambda_km)
                temporal_w, _ = self._temporal_weight(context, w, self.time_lambda_days)
                depth_w, _ = self._depth_weight(context.event_depth_km, w.top_inj_zone, w.bot_inj_zone, self.depth_sigma_km)
                rate_w = self._rate_boost(w, self.rate_boost_cap)
                total += w.cumulative_bbl * spatial_w * temporal_w * depth_w * rate_w
        return total

    def _frac_score(self, context: EventContextOut) -> float:
        total = 0.0
        for j in context.nearby_frac_jobs:
            wv_bbl = (j.total_water_volume or 0.0) / _GAL_PER_BBL
            if wv_bbl > 0 and j.distance_km > 0:
                spatial_w = math.exp(-j.distance_km / self.frac_lambda_km)
                depth_w, _ = self._depth_weight(context.event_depth_km, j.formation_depth, j.formation_depth, self.depth_sigma_km)
                total += wv_bbl * spatial_w * depth_w
        return total
