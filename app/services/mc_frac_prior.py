"""Monte Carlo frac uncertainty layer.

When FracFocus returns zero nearby jobs, zero frac_score is epistemically false:
FracFocus has structural underreporting. This module estimates a distribution of
plausible frac contributions by sampling synthetic jobs from a prior fitted to the
broader Delaware Basin FracFocus dataset (or falling back to published defaults when
the broader query also returns too few rows).

Usage
-----
1. Call build_prior_from_jobs() with a wider-radius FracFocus query to get FracPriorParams.
2. Pass params to MonteCarloFracSampler.sample() to get (mean, p5, p95) of frac_score.
3. Attach FracPriorParams to EventContextOut.frac_prior_params; PhysicsAttributionService
   reads it and calls the sampler automatically.
"""
import math
import random
import logging
from typing import Optional

from app.schemas.analysis import FracPriorParams

log = logging.getLogger(__name__)

_GAL_PER_BBL = 42.0
_FT_TO_KM = 0.0003048

# Delaware Basin defaults derived from published literature.
# Wolfcamp / Bone Spring completions (Permian Basin operators, 2015–2023).
# Water vol: median ≈ 12.5M gal ≈ 298k bbl; log-std ≈ 0.8 (wide, heavy-tailed distribution).
# TVD: Wolfcamp A/B/C center ≈ 7 500 ft, σ ≈ 1 500 ft.
# n_jobs_mean = 2.0: ~2 unreported jobs expected inside a 10 km / 2-year search window.
DELAWARE_BASIN_DEFAULTS = FracPriorParams(
    source="basin_defaults",
    sample_size=0,
    n_jobs_mean=2.0,
    water_vol_log_mean=12.6,
    water_vol_log_std=0.8,
    depth_mean_ft=7500.0,
    depth_std_ft=1500.0,
)

_MIN_ROWS_FOR_FIT = 10

# When the broader area has >= this many rows, FracFocus coverage is considered
# "well-sampled" and an inner-area zero is treated as a genuine absence, not a gap.
_COVERAGE_THRESHOLD = 50

# Texas FracFocus non-compliance estimate (~10% of operators don't report).
# Applied as a multiplier to n_jobs_mean when coverage is good but inner count is 0.
_TX_UNDERREPORT_RATE = 0.10


def build_prior_from_jobs(
    rows: list[dict],
    search_radius_km: float,
    broader_radius_km: float,
    inner_job_count: int = 0,
) -> FracPriorParams:
    """Fit a FracPriorParams from FracFocus rows queried over a broader area.

    Scales the observed job count down to the search area using the ratio of areas.
    When the broader area is well-sampled (>= _COVERAGE_THRESHOLD rows) and the
    inner search found 0 jobs, the absence is treated as mostly real: n_jobs_mean
    is discounted by _TX_UNDERREPORT_RATE (~10%) to represent only unreported jobs.
    Falls back to DELAWARE_BASIN_DEFAULTS when the sample is too sparse to fit.
    """
    vol_bbls: list[float] = []
    depths_ft: list[float] = []

    for row in rows:
        raw_v = row.get("totalbasewatervolume")
        if raw_v not in (None, "", "None"):
            try:
                v = float(raw_v) / _GAL_PER_BBL
                if v > 0:
                    vol_bbls.append(v)
            except (TypeError, ValueError):
                pass

        raw_d = row.get("tvd")
        if raw_d not in (None, "", "None"):
            try:
                d = float(raw_d)
                if d > 0:
                    depths_ft.append(d)
            except (TypeError, ValueError):
                pass

    if len(vol_bbls) < _MIN_ROWS_FOR_FIT:
        log.debug(
            f"MC prior: only {len(vol_bbls)} valid volume rows in broader area; "
            "using basin defaults"
        )
        return DELAWARE_BASIN_DEFAULTS

    # Fit log-normal to water volumes
    log_vols = [math.log(v) for v in vol_bbls]
    log_mean = sum(log_vols) / len(log_vols)
    variance = sum((x - log_mean) ** 2 for x in log_vols) / len(log_vols)
    log_std = math.sqrt(variance) if variance > 0 else 0.2

    # Fit normal to TVD depths; fall back to defaults if too few
    if len(depths_ft) >= 5:
        depth_mean = sum(depths_ft) / len(depths_ft)
        dvar = sum((x - depth_mean) ** 2 for x in depths_ft) / len(depths_ft)
        depth_std = math.sqrt(dvar) if dvar > 0 else DELAWARE_BASIN_DEFAULTS.depth_std_ft
    else:
        depth_mean = DELAWARE_BASIN_DEFAULTS.depth_mean_ft
        depth_std = DELAWARE_BASIN_DEFAULTS.depth_std_ft

    # Spatial rescaling: broader area / search area = (r_broader / r_search)²
    area_ratio = (search_radius_km / broader_radius_km) ** 2
    n_jobs_mean_raw = len(rows) * area_ratio

    # Coverage-aware discount: if FracFocus is well-sampled in the broader area AND
    # found 0 jobs in the inner search area, the absence is mostly real. Only the
    # Texas non-compliance fraction (~10%) should contribute to n_jobs_mean.
    well_sampled = len(rows) >= _COVERAGE_THRESHOLD
    coverage_adjusted = well_sampled and inner_job_count == 0
    if coverage_adjusted:
        n_jobs_mean = max(n_jobs_mean_raw * _TX_UNDERREPORT_RATE, 0.1)
        source = "data_driven_adjusted"
    else:
        n_jobs_mean = max(n_jobs_mean_raw, 0.1)
        source = "data_driven"

    log.debug(
        f"MC prior: {source} from {len(rows)} broader rows, "
        f"n_jobs_mean_raw={n_jobs_mean_raw:.2f} → n_jobs_mean={n_jobs_mean:.2f}"
        + (" (coverage discount applied)" if coverage_adjusted else "")
        + f", log_mean={log_mean:.2f}, log_std={log_std:.2f}"
    )
    return FracPriorParams(
        source=source,
        sample_size=len(rows),
        n_jobs_mean=n_jobs_mean,
        water_vol_log_mean=log_mean,
        water_vol_log_std=max(log_std, 0.1),
        depth_mean_ft=depth_mean,
        depth_std_ft=max(depth_std, 100.0),
    )


def _poisson_sample(rng: random.Random, lam: float) -> int:
    """Knuth algorithm for Poisson sampling. Suitable for λ < ~30."""
    if lam <= 0:
        return 0
    # Protect against underflow when lam is large
    L = math.exp(-min(lam, 500.0))
    k, p = 0, 1.0
    while p > L:
        k += 1
        p *= rng.random()
    return k - 1


class MonteCarloFracSampler:
    """Samples synthetic frac scores to quantify uncertainty when observed frac data is absent.

    Each trial:
      1. Draws n_jobs from Poisson(params.n_jobs_mean)
      2. For each job draws distance (area-weighted uniform on disk), water volume
         (log-normal), and TVD depth (normal)
      3. Computes frac_score using the same spatial-decay + depth-penalty formula
         as PhysicsAttributionService._frac_score

    Returns (mean, p5, p95) across n_trials.
    """

    def sample(
        self,
        params: FracPriorParams,
        event_depth_km: Optional[float],
        frac_radius_km: float,
        frac_lambda_km: float,
        depth_sigma_km: float,
        n_trials: int = 1000,
        seed: Optional[int] = None,
    ) -> tuple[float, float, float]:
        rng = random.Random(seed)
        scores: list[float] = []

        for _ in range(n_trials):
            n_jobs = _poisson_sample(rng, params.n_jobs_mean)
            trial_score = 0.0

            for _ in range(n_jobs):
                # Area-weighted distance: CDF of P(r) ∝ r on [0, R] → r = R√U
                u = rng.random()
                d_km = frac_radius_km * math.sqrt(u) if u > 0 else 0.001

                # Log-normal water volume (bbl)
                wv_bbl = math.exp(rng.gauss(params.water_vol_log_mean, params.water_vol_log_std))

                # Depth (ft)
                depth_ft = rng.gauss(params.depth_mean_ft, params.depth_std_ft)

                # Spatial exponential decay (matches existing frac scoring)
                spatial_w = math.exp(-d_km / frac_lambda_km) if d_km > 0 else 0.0

                # Depth Gaussian penalty (matches HeuristicAttributionService._depth_weight)
                if event_depth_km is not None:
                    mid_km = depth_ft * _FT_TO_KM
                    delta_km = abs(event_depth_km - mid_km)
                    depth_w = math.exp(-(delta_km ** 2) / (2.0 * depth_sigma_km ** 2))
                else:
                    depth_w = 1.0

                trial_score += wv_bbl * spatial_w * depth_w

            scores.append(trial_score)

        scores.sort()
        mean_score = sum(scores) / n_trials
        idx_p5 = max(0, int(0.05 * n_trials) - 1)
        idx_p95 = min(n_trials - 1, int(0.95 * n_trials))
        return round(mean_score, 4), round(scores[idx_p5], 4), round(scores[idx_p95], 4)
