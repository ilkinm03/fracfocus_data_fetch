"""Pore-pressure diffusion attribution engine — physics_v2.

Improvements over physics_v1
-----------------------------
1. Per-well hydraulic diffusivity D from Delaware Basin formation lookup
   (replaces single 0.5 m²/s constant; see app/utils/formation_lookup.py).
   D now varies from 0.001 m²/s (evaporites) to 0.6 m²/s (Bone Spring).
2. Coulomb Failure Function (CFF) boost: H-10 average injection pressure
   is converted to ΔP (MPa) and used to compute a CFF weight that multiplies
   each well's score when the fault is in a favourable orientation relative
   to the Delaware Basin regional stress (σ_Hmax N45°E).
3. Injection duration: record-count fallback (monthly_record_count × 30.44 d)
   is used when first_report_date is unavailable, giving a better estimate
   for established wells whose earliest record pre-dates the search window.
4. Formation name and D value are surfaced in signal descriptions for
   transparency and auditability.

Physics basis (unchanged from v1)
----------------------------------
SWD:  Σ_i  cumulative_bbl[i]
            × erfc(r[i] / 2√(D_i · t_i))   Shapiro / Theis diffusion
            × exp(−Δdepth² / 2σ²)           depth mismatch penalty
            × min(rate_ratio[i], cap)        injection rate boost
            × (1 + cff_weight[i])           Coulomb failure boost  ← new

Frac: Σ_j  (water_vol_gal[j] / 42)
            × exp(−r[j] / λ_frac)           spatial decay
            × exp(−Δdepth² / 2σ²)           depth mismatch penalty

MC:   when frac data absent → MonteCarloFracSampler (numpy, 2 000 trials)

References
----------
Shapiro S.A., Huenges E., Borm G. (1997). GJI 131(2), F15–F18.
Smye K. et al. (2024). Hydraulic diffusivity in the Delaware Basin.
King G. et al. (1994). Static stress changes and the triggering of earthquakes.
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
)
from app.services.mc_frac_prior import MonteCarloFracSampler
from app.utils.formation_lookup import get_diffusivity, get_formation_name
from app.services.coulomb_service import cff_weight_from_psi

log = logging.getLogger(__name__)

_ENGINE = "physics_v2"

_SECONDS_PER_DAY: float = 86_400.0
_METERS_PER_KM: float = 1_000.0
_MIN_INJECT_DAYS: float = 30.0


class PhysicsAttributionService:
    """SWD attribution via pore-pressure diffusion with formation-specific D and CFF.

    Parameters
    ----------
    d_swd_override : float or None
        When set, overrides the per-well formation D with a single constant.
        Intended for calibration grid search (calibrate_engine.py --engine physics).
        When None (default), D is taken from the Delaware Basin formation table.
    frac_lambda_km : float
        Spatial decay length-scale for frac (km).
    depth_sigma_km : float
        Gaussian σ for depth-mismatch penalty (km).
    rate_boost_cap : float
        Maximum rate-change multiplier for SWD.
    apply_cff : bool
        When True, applies Coulomb Failure Function boost to SWD scores using
        H-10 average injection pressure. Set False in calibration mode to
        isolate diffusion parameters.
    mc_n_trials : int
        Monte Carlo trials for frac uncertainty quantification.
    """

    def __init__(
        self,
        d_swd_override: float | None = None,
        frac_lambda_km: float = _FRAC_LAMBDA_KM,
        depth_sigma_km: float = _DEPTH_SIGMA_KM,
        rate_boost_cap: float = _RATE_BOOST_CAP,
        apply_cff: bool = True,
        mc_n_trials: int = 2000,
    ) -> None:
        self.d_swd_override = d_swd_override
        self.frac_lambda_km = frac_lambda_km
        self.depth_sigma_km = depth_sigma_km
        self.rate_boost_cap = rate_boost_cap
        self.apply_cff = apply_cff
        self._mc_sampler = MonteCarloFracSampler()
        self._mc_n_trials = mc_n_trials

    # ------------------------------------------------------------------
    # Public interface (same contract as HeuristicAttributionService)
    # ------------------------------------------------------------------

    def score(self, context: EventContextOut) -> AttributionResult:
        swd_score = self._swd_score(context)
        frac_score = self._frac_score(context)

        frac_data_quality = "observed" if context.nearby_frac_jobs else "absent"
        driver, confidence = HeuristicAttributionService._verdict(swd_score, frac_score)

        mc_mean = mc_p5 = mc_p95 = None
        adj_driver = adj_conf = None
        if context.frac_prior_params is not None:
            mc_mean, mc_p5, mc_p95 = self._mc_sampler.sample(
                context.frac_prior_params,
                event_depth_km=context.event_depth_km,
                frac_radius_km=context.frac_radius_km,
                frac_lambda_km=self.frac_lambda_km,
                depth_sigma_km=self.depth_sigma_km,
                n_trials=self._mc_n_trials,
            )
            adj_driver, adj_conf = HeuristicAttributionService._verdict(swd_score, mc_mean)

        signals: list[AttributionSignal] = []
        cff_was_applied = False

        for w in context.nearby_swd_wells:
            if w.cumulative_bbl <= 0:
                continue
            t_s = self._inject_duration_s(context, w)
            d_val = self._well_diffusivity(w)
            diff_w = self._diffusion_weight(w.distance_km, t_s, d_val)
            depth_w, delta_km = HeuristicAttributionService._depth_weight(
                context.event_depth_km, w.top_inj_zone, w.bot_inj_zone, self.depth_sigma_km
            )
            rate_w = HeuristicAttributionService._rate_boost(w, self.rate_boost_cap)

            cff_w = 0.0
            if self.apply_cff:
                cff_w = cff_weight_from_psi(w.avg_pressure_psi)
                if cff_w > 0:
                    cff_was_applied = True

            weighted_val = w.cumulative_bbl * diff_w * depth_w * rate_w * (1.0 + cff_w)

            front_km = self._pressure_front_km(t_s, d_val)
            form_name = get_formation_name(w.top_inj_zone, w.bot_inj_zone)
            depth_note = f", depth Δ{delta_km} km" if delta_km is not None else ""
            rate_note = (
                f", rate ×{w.rate_change_ratio:.2f} (capped ×{rate_w:.2f})"
                if w.rate_change_ratio is not None else ""
            )
            cff_note = f", CFF ×{1.0 + cff_w:.3f}" if cff_w > 0 else ""
            signals.append(
                AttributionSignal(
                    name=f"SWD {w.uic_number}",
                    value=round(weighted_val, 2),
                    unit="weighted_bbl",
                    description=(
                        f"{w.uic_number} — {w.distance_km} km, "
                        f"{w.cumulative_bbl:,.0f} bbl, "
                        f"front≈{front_km:.1f} km, erfc={diff_w:.4f}, "
                        f"D={d_val:.3f} m²/s [{form_name}]"
                        f"{depth_note}{rate_note}{cff_note}"
                    ),
                )
            )

        for j in context.nearby_frac_jobs:
            wv_bbl = (j.total_water_volume or 0.0) / _GAL_PER_BBL
            if wv_bbl <= 0:
                continue
            spatial_w = math.exp(-j.distance_km / self.frac_lambda_km)
            depth_w, delta_km = HeuristicAttributionService._depth_weight(
                context.event_depth_km, j.formation_depth, j.formation_depth, self.depth_sigma_km
            )
            weighted_val = wv_bbl * spatial_w * depth_w
            depth_note = f", depth Δ{delta_km} km" if delta_km is not None else ""
            depth_src = f" [{j.depth_source}]" if j.depth_source else ""
            signals.append(
                AttributionSignal(
                    name=f"FRAC {j.api_number or j.well_name or 'unknown'}",
                    value=round(weighted_val, 2),
                    unit="weighted_bbl",
                    description=(
                        f"Frac job at {j.distance_km} km, started {j.job_start_date}, "
                        f"{j.total_water_volume:,.0f} gal ({wv_bbl:,.0f} bbl)"
                        f"{depth_note}{depth_src}"
                    ),
                )
            )

        if mc_mean is not None and mc_mean > 0:
            prior = context.frac_prior_params
            signals.append(
                AttributionSignal(
                    name="FRAC [MC estimate]",
                    value=round(mc_mean, 2),
                    unit="weighted_bbl",
                    description=(
                        f"Monte Carlo frac estimate (N={self._mc_n_trials}): "
                        f"mean={mc_mean:,.0f}, p5={mc_p5:,.0f}, p95={mc_p95:,.0f} — "
                        f"prior source={prior.source}, sample_size={prior.sample_size}, "
                        f"n_jobs_mean={prior.n_jobs_mean:.2f}"
                    ),
                )
            )

        signals.sort(key=lambda s: s.value, reverse=True)

        mc_part = (
            f"mc_frac_mean={mc_mean:.2f} (p5={mc_p5:.2f} p95={mc_p95:.2f}) | "
            f"adj={adj_driver}/{adj_conf:.4f} | "
            if mc_mean is not None else ""
        )
        log.info(
            f"Attribution [{_ENGINE}] event={context.event_id}: "
            f"driver={driver} conf={confidence} | "
            f"swd={swd_score:.2f} frac={frac_score:.2f} | "
            f"{mc_part}cff={cff_was_applied}"
        )

        return AttributionResult(
            engine=_ENGINE,
            likely_driver=driver,
            confidence=confidence,
            swd_score=round(swd_score, 4),
            frac_score=round(frac_score, 4),
            signals=signals,
            frac_data_quality=frac_data_quality,
            mc_frac_score_mean=mc_mean,
            mc_frac_score_p5=mc_p5,
            mc_frac_score_p95=mc_p95,
            adjusted_likely_driver=adj_driver,
            adjusted_confidence=adj_conf,
            cff_applied=cff_was_applied,
        )

    # ------------------------------------------------------------------
    # Physics helpers
    # ------------------------------------------------------------------

    def _well_diffusivity(self, w) -> float:
        """Return D (m²/s): calibration override takes precedence over formation lookup."""
        if self.d_swd_override is not None:
            return self.d_swd_override
        return get_diffusivity(w.top_inj_zone, w.bot_inj_zone)

    def _inject_duration_s(self, context: EventContextOut, w) -> float:
        """Estimate injection duration before the event (seconds).

        Priority:
        1. first_report_date in the search window  → exact date difference
        2. monthly_record_count × 30.44 days       → record-count fallback
           (not bounded by the search window, so old wells aren't underestimated)
        3. Floor: _MIN_INJECT_DAYS (30 d)          → prevents t = 0 singularity
        """
        if context.event_date is not None and w.first_report_date is not None:
            days = (context.event_date - w.first_report_date).days
        elif w.monthly_record_count > 0:
            days = w.monthly_record_count * 30.44
        else:
            days = _MIN_INJECT_DAYS
        return max(days, _MIN_INJECT_DAYS) * _SECONDS_PER_DAY

    def _diffusion_weight(self, distance_km: float, t_inject_s: float, d_m2_s: float) -> float:
        """erfc(r / 2√(D·t)) — 1.0 at wellbore, 0.0 far field."""
        r_m = distance_km * _METERS_PER_KM
        diffusion_length = 2.0 * math.sqrt(d_m2_s * t_inject_s)
        if diffusion_length == 0.0:
            return 0.0
        return math.erfc(r_m / diffusion_length)

    def _pressure_front_km(self, t_inject_s: float, d_m2_s: float) -> float:
        """Shapiro triggering front radius (km): r_front = √(4π·D·t)."""
        return math.sqrt(4.0 * math.pi * d_m2_s * t_inject_s) / _METERS_PER_KM

    # ------------------------------------------------------------------
    # Score accumulators
    # ------------------------------------------------------------------

    def _swd_score(self, context: EventContextOut) -> float:
        total = 0.0
        for w in context.nearby_swd_wells:
            if w.cumulative_bbl <= 0 or w.distance_km <= 0:
                continue
            t_s = self._inject_duration_s(context, w)
            d_val = self._well_diffusivity(w)
            diff_w = self._diffusion_weight(w.distance_km, t_s, d_val)
            depth_w, _ = HeuristicAttributionService._depth_weight(
                context.event_depth_km, w.top_inj_zone, w.bot_inj_zone, self.depth_sigma_km
            )
            rate_w = HeuristicAttributionService._rate_boost(w, self.rate_boost_cap)
            cff_w = cff_weight_from_psi(w.avg_pressure_psi) if self.apply_cff else 0.0
            total += w.cumulative_bbl * diff_w * depth_w * rate_w * (1.0 + cff_w)
        return total

    def _frac_score(self, context: EventContextOut) -> float:
        total = 0.0
        for j in context.nearby_frac_jobs:
            wv_bbl = (j.total_water_volume or 0.0) / _GAL_PER_BBL
            if wv_bbl <= 0 or j.distance_km <= 0:
                continue
            spatial_w = math.exp(-j.distance_km / self.frac_lambda_km)
            depth_w, _ = HeuristicAttributionService._depth_weight(
                context.event_depth_km, j.formation_depth, j.formation_depth, self.depth_sigma_km
            )
            total += wv_bbl * spatial_w * depth_w
        return total
