"""Pore-pressure diffusion attribution engine (Shapiro et al. 1997).

Replaces the static exponential spatial decay used by the heuristic engine with a
physically derived pressure-influence function based on the complementary error
function (erfc). The key difference:

  Heuristic:  weight = exp(−r / λ)                      [static; λ is a tuned constant]
  Physics:    weight = erfc(r / 2√(D·t))                [time-dependent; D is diffusivity]

where:
  r  = distance from well to earthquake (metres)
  D  = hydraulic diffusivity of the host formation (m²/s)
  t  = estimated injection duration (seconds)

The erfc formulation naturally captures that a well 15 km away which has been
injecting for 1 year should have negligible influence (pressure front hasn't
arrived), while the same well after 5 years of injection has meaningful influence.
The heuristic model cannot distinguish these two cases.

Frac scoring is inherited unchanged from the heuristic engine (spatial + depth
decay), since poroelastic stress from fracking is nearly instantaneous and does
not require a diffusion model.

References
----------
Shapiro S.A., Huenges E., Borm G. (1997). Estimating the crust permeability from
fluid-injection-induced seismic emission at the KTB site. Geophysical Journal
International, 131(2), F15–F18.

Smye K. et al. (2024). Hydraulic diffusivity in the Delaware Basin from SWD-induced
seismicity migration rates. [Internal BEG report — replace with published citation.]
"""

import math
import logging
from app.schemas.analysis import AttributionResult, AttributionSignal, EventContextOut
from app.services.attribution_service import (
    HeuristicAttributionService,
    _FRAC_LAMBDA_KM,
    _DEPTH_SIGMA_KM,
    _RATE_BOOST_CAP,
    _GAL_PER_BBL,
    _FT_TO_KM,
)

log = logging.getLogger(__name__)

_ENGINE = "physics_v1"

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------

# Hydraulic diffusivity for Delaware Basin Wolfcamp / Bone Spring formations.
# Published range: 0.01–5 m²/s.  Smye et al. (2024) estimate 0.1–1.0 m²/s.
# Default: 0.5 m²/s (geometric mean of reported range).  Injectable for calibration.
_D_SWD_M2_S: float = 0.5

_SECONDS_PER_DAY: float = 86_400.0
_METERS_PER_KM: float = 1_000.0

# Minimum injection duration fed into the diffusion formula.
# Prevents singularity at t = 0 and handles wells with a single H-10 record.
_MIN_INJECT_DAYS: float = 30.0


class PhysicsAttributionService:
    """SWD attribution via pore-pressure diffusion; frac attribution via spatial decay.

    SWD score = Σ  cumulative_bbl[i]
                    × erfc( r[i] / 2√(D · t_inject[i]) )   # diffusion influence
                    × exp( −Δdepth[i]² / 2σ² )             # depth mismatch penalty
                    × min( rate_change_ratio[i], cap )      # injection rate boost

    Frac score = Σ  (water_vol_gal[i] / 42)
                     × exp( −r[i] / λ_frac )               # spatial decay
                     × exp( −Δdepth[i]² / 2σ² )            # depth mismatch penalty

    Parameters
    ----------
    d_swd_m2_s : float
        Hydraulic diffusivity D (m²/s). Higher values → faster pressure propagation
        → distant wells score higher. Calibrate against ground-truth labels using
        scripts/calibrate_engine.py --engine physics.
    frac_lambda_km : float
        Spatial decay length-scale for frac (km). Inherited from heuristic engine.
    depth_sigma_km : float
        Gaussian σ for depth-mismatch penalty (km). Inherited from heuristic engine.
    rate_boost_cap : float
        Maximum rate-change multiplier for SWD. Inherited from heuristic engine.
    """

    def __init__(
        self,
        d_swd_m2_s: float = _D_SWD_M2_S,
        frac_lambda_km: float = _FRAC_LAMBDA_KM,
        depth_sigma_km: float = _DEPTH_SIGMA_KM,
        rate_boost_cap: float = _RATE_BOOST_CAP,
    ) -> None:
        self.d_swd_m2_s = d_swd_m2_s
        self.frac_lambda_km = frac_lambda_km
        self.depth_sigma_km = depth_sigma_km
        self.rate_boost_cap = rate_boost_cap

    # ------------------------------------------------------------------
    # Public interface (same contract as HeuristicAttributionService)
    # ------------------------------------------------------------------

    def score(self, context: EventContextOut) -> AttributionResult:
        swd_score = self._swd_score(context)
        frac_score = self._frac_score(context)

        driver, confidence = HeuristicAttributionService._verdict(swd_score, frac_score)

        signals: list[AttributionSignal] = []

        for w in context.nearby_swd_wells:
            if w.cumulative_bbl > 0:
                t_s = self._inject_duration_s(context, w)
                diff_w = self._diffusion_weight(w.distance_km, t_s)
                depth_w, delta_km = HeuristicAttributionService._depth_weight(
                    context.event_depth_km, w.top_inj_zone, w.bot_inj_zone, self.depth_sigma_km
                )
                rate_w = HeuristicAttributionService._rate_boost(w, self.rate_boost_cap)
                weighted_val = w.cumulative_bbl * diff_w * depth_w * rate_w

                front_km = self._pressure_front_km(t_s)
                depth_note = f", depth Δ{delta_km} km" if delta_km is not None else ""
                rate_note = (
                    f", rate ×{w.rate_change_ratio:.2f} (capped ×{rate_w:.2f})"
                    if w.rate_change_ratio is not None else ""
                )
                signals.append(
                    AttributionSignal(
                        name=f"SWD {w.uic_number}",
                        value=round(weighted_val, 2),
                        unit="weighted_bbl",
                        description=(
                            f"{w.uic_number} — {w.distance_km} km away, "
                            f"{w.cumulative_bbl:,.0f} bbl cumul., "
                            f"pressure front ≈{front_km:.1f} km, "
                            f"erfc={diff_w:.4f}"
                            f"{depth_note}{rate_note}"
                        ),
                    )
                )

        for j in context.nearby_frac_jobs:
            wv_bbl = (j.total_water_volume or 0.0) / _GAL_PER_BBL
            if wv_bbl > 0:
                spatial_w = math.exp(-j.distance_km / self.frac_lambda_km)
                depth_w, delta_km = HeuristicAttributionService._depth_weight(
                    context.event_depth_km, j.formation_depth, j.formation_depth, self.depth_sigma_km
                )
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

        signals.sort(key=lambda s: s.value, reverse=True)

        log.info(
            f"Attribution [{_ENGINE}] event={context.event_id}: "
            f"driver={driver} confidence={confidence} "
            f"swd_score={swd_score:.2f} frac_score={frac_score:.2f} "
            f"D={self.d_swd_m2_s} m²/s"
        )
        return AttributionResult(
            engine=_ENGINE,
            likely_driver=driver,
            confidence=confidence,
            swd_score=round(swd_score, 4),
            frac_score=round(frac_score, 4),
            signals=signals,
        )

    # ------------------------------------------------------------------
    # Physics helpers
    # ------------------------------------------------------------------

    def _inject_duration_s(self, context: EventContextOut, w) -> float:
        """Estimate how long the well has been injecting before the event, in seconds.

        Uses first_report_date (earliest H-10 record in the search window) relative
        to the event date. Falls back to monthly_record_count × 30.44 days when the
        first record date is unavailable. A floor of _MIN_INJECT_DAYS prevents
        singularity at t = 0 and treats wells with a single record conservatively.

        Note: first_report_date is bounded by the search window start (default 10
        years). Wells older than the window will have their duration capped at the
        window length — a conservative underestimate.
        """
        if context.event_date is not None and w.first_report_date is not None:
            days = (context.event_date - w.first_report_date).days
        elif w.monthly_record_count > 0:
            days = w.monthly_record_count * 30.44
        else:
            days = _MIN_INJECT_DAYS
        return max(days, _MIN_INJECT_DAYS) * _SECONDS_PER_DAY

    def _diffusion_weight(self, distance_km: float, t_inject_s: float) -> float:
        """Complementary error function pressure influence.

        erfc(r / 2√(D·t)) — ranges from 1.0 (at the wellbore) to 0.0 (far field).

        Geophysical interpretation: erfc measures how much of the injected pressure
        wave has reached distance r after injection for time t with diffusivity D.
        A value of 0.0 means the pressure front has not yet arrived; 1.0 means full
        pressure perturbation has been established.
        """
        r_m = distance_km * _METERS_PER_KM
        diffusion_length = 2.0 * math.sqrt(self.d_swd_m2_s * t_inject_s)
        if diffusion_length == 0.0:
            return 0.0
        return math.erfc(r_m / diffusion_length)

    def _pressure_front_km(self, t_inject_s: float) -> float:
        """Shapiro triggering front radius (km): r_front = √(4π·D·t).

        Returned purely for display in signal descriptions — the front radius tells
        investigators how far pressure has theoretically propagated for this well.
        """
        return math.sqrt(4.0 * math.pi * self.d_swd_m2_s * t_inject_s) / _METERS_PER_KM

    # ------------------------------------------------------------------
    # Score accumulators
    # ------------------------------------------------------------------

    def _swd_score(self, context: EventContextOut) -> float:
        total = 0.0
        for w in context.nearby_swd_wells:
            if w.cumulative_bbl > 0 and w.distance_km > 0:
                t_s = self._inject_duration_s(context, w)
                diff_w = self._diffusion_weight(w.distance_km, t_s)
                depth_w, _ = HeuristicAttributionService._depth_weight(
                    context.event_depth_km, w.top_inj_zone, w.bot_inj_zone, self.depth_sigma_km
                )
                rate_w = HeuristicAttributionService._rate_boost(w, self.rate_boost_cap)
                total += w.cumulative_bbl * diff_w * depth_w * rate_w
        return total

    def _frac_score(self, context: EventContextOut) -> float:
        total = 0.0
        for j in context.nearby_frac_jobs:
            wv_bbl = (j.total_water_volume or 0.0) / _GAL_PER_BBL
            if wv_bbl > 0 and j.distance_km > 0:
                spatial_w = math.exp(-j.distance_km / self.frac_lambda_km)
                depth_w, _ = HeuristicAttributionService._depth_weight(
                    context.event_depth_km, j.formation_depth, j.formation_depth, self.depth_sigma_km
                )
                total += wv_bbl * spatial_w * depth_w
        return total
