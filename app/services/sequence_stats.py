"""Seismic sequence statistics and ETAS declustering.

Provides functions to characterise seismic sequences and identify aftershocks
using the Epidemic Type Aftershock Sequence (ETAS) model (Ogata 1988).

Public API
----------
b_value_mle(magnitudes, mc)           — Aki (1965) MLE b-value estimator
omori_p_value(times_days)             — Utsu (1961) p-value via grid search
interevent_cv(times_days)             — coefficient of variation of interevent times
cusum_rate_shift(times_days)          — CUSUM detection of anomalous rate increase
etas_decluster(events, params, mc)    — Ogata (1988) stochastic declustering

References
----------
Aki K. (1965). Maximum likelihood estimate of b in the formula log N = a − bM.
Utsu T. (1961). A statistical study on the occurrence of aftershocks.
Ogata Y. (1988). Statistical models for earthquake occurrences.
Zhuang J. et al. (2002). Stochastic declustering of space-time earthquake occurrences.
Llenos A., Michael A. (2013). Modeling earthquake rate changes in Oklahoma.
"""

import math
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# b-value MLE
# ---------------------------------------------------------------------------

def b_value_mle(magnitudes: list[float], mc: float = 2.0) -> Optional[float]:
    """Aki (1965) maximum likelihood b-value: b = log10(e) / (mean(M) − mc).

    Parameters
    ----------
    magnitudes : event magnitudes (values below mc are excluded)
    mc         : magnitude of completeness

    Returns None when fewer than 5 events are above mc or when the
    distribution is degenerate (mean == mc).
    """
    above = [m for m in magnitudes if m >= mc]
    if len(above) < 5:
        return None
    mean_m = sum(above) / len(above)
    delta = mean_m - mc
    if delta <= 0.0:
        return None
    return round(math.log10(math.e) / delta, 4)


# ---------------------------------------------------------------------------
# Omori p-value
# ---------------------------------------------------------------------------

def omori_p_value(
    times_days: list[float],
    reference_time: float = 0.0,
    c: float = 0.01,
) -> Optional[float]:
    """Estimate Omori-Utsu p-value from aftershock occurrence times.

    Uses a 1-D grid search over p ∈ [0.50, 2.00] maximising the log-likelihood
    of the Omori-Utsu intensity λ(t) = K / (t + c)^p. K cancels in the
    p-only optimisation, leaving LL(p) = −p · Σ log(t_i + c).

    Parameters
    ----------
    times_days      : event times in days from an arbitrary epoch
    reference_time  : main-shock time; only events after this are used
    c               : small offset (days) preventing the t = 0 singularity

    Returns None when fewer than 5 aftershocks are available.
    """
    aftershocks = [t - reference_time for t in times_days if t > reference_time]
    if len(aftershocks) < 5:
        return None

    best_p, best_ll = 1.0, float("-inf")
    for p100 in range(50, 201):      # p from 0.50 to 2.00, step 0.01
        p = p100 / 100.0
        ll = sum(-p * math.log(t + c) for t in aftershocks)
        if ll > best_ll:
            best_ll, best_p = ll, p

    return round(best_p, 2)


# ---------------------------------------------------------------------------
# Interevent coefficient of variation
# ---------------------------------------------------------------------------

def interevent_cv(times_days: list[float]) -> Optional[float]:
    """CV (std / mean) of interevent times.

    CV ≈ 1.0  → Poisson / random background
    CV < 1.0  → quasi-periodic (tectonic swarm)
    CV > 1.0  → clustered (aftershock sequence or injection burst)

    Returns None when fewer than 3 events are supplied.
    """
    if len(times_days) < 3:
        return None
    s = sorted(times_days)
    diffs = [s[i + 1] - s[i] for i in range(len(s) - 1)]
    mean = sum(diffs) / len(diffs)
    if mean == 0.0:
        return None
    var = sum((d - mean) ** 2 for d in diffs) / len(diffs)
    return round(math.sqrt(var) / mean, 4)


# ---------------------------------------------------------------------------
# CUSUM rate-shift detection
# ---------------------------------------------------------------------------

def cusum_rate_shift(
    times_days: list[float],
    baseline_rate_per_day: Optional[float] = None,
) -> Optional[float]:
    """CUSUM peak statistic (dimensionless rate multiplier).

    Computes S_k = max(0, S_{k-1} + N_k − baseline) over daily bins then
    returns max(S) / baseline. A value > 10 indicates a statistically
    significant rate acceleration.

    Parameters
    ----------
    times_days            : event times in days from an arbitrary epoch
    baseline_rate_per_day : expected daily rate; estimated from the first
                            half of the record when None

    Returns None when the time span is < 7 days.
    """
    if not times_days:
        return None
    t_min, t_max = min(times_days), max(times_days)
    if (t_max - t_min) < 7.0:
        return None

    n_days = max(1, int(t_max - t_min))
    daily = [0.0] * n_days
    for t in times_days:
        idx = min(int(t - t_min), n_days - 1)
        daily[idx] += 1.0

    if baseline_rate_per_day is None:
        half = max(1, n_days // 2)
        baseline_rate_per_day = sum(daily[:half]) / half

    if baseline_rate_per_day <= 0.0:
        return None

    cusum, peak = 0.0, 0.0
    for count in daily:
        cusum = max(0.0, cusum + count - baseline_rate_per_day)
        peak = max(peak, cusum)

    return round(peak / baseline_rate_per_day, 2)


# ---------------------------------------------------------------------------
# ETAS declustering
# ---------------------------------------------------------------------------

@dataclass
class ETASParams:
    """ETAS model parameters.

    Default values calibrated for Texas / Delaware Basin sequences using
    Llenos & Michael (2013) regional estimates.

    mu    : background rate (events/day)
    K     : aftershock productivity coefficient
    alpha : magnitude sensitivity of productivity
    c     : Omori time offset (days) — prevents singularity at t = 0
    p     : Omori temporal decay exponent
    """
    mu:    float = 0.05
    K:     float = 0.08
    alpha: float = 1.5
    c:     float = 0.01
    p:     float = 1.1


@dataclass
class ETASEvent:
    event_id: str
    time_days: float          # days from catalog start, monotonically increasing
    magnitude: float
    is_background: Optional[bool] = None
    background_probability: Optional[float] = None


def etas_decluster(
    events: list[ETASEvent],
    params: Optional[ETASParams] = None,
    mc: float = 2.0,
    n_iterations: int = 5,
) -> list[ETASEvent]:
    """Stochastic ETAS declustering (Zhuang et al. 2002 / Ogata 1988).

    Iteratively assigns background probability φ_i to each event:

        φ_i = μ / (μ + Σ_{j<i} φ_j · λ_ji)

    where λ_ji = K · exp(α(m_j − mc)) / (t_i − t_j + c)^p is the
    triggered rate contribution from event j to i.

    The spatial kernel is collapsed (spatial-free) because the attribution
    engine handles spatial weighting independently.

    Parameters
    ----------
    events       : list of ETASEvent (sorted internally by time)
    params       : ETAS parameters; uses ETASParams() defaults when None
    mc           : magnitude of completeness
    n_iterations : EM iterations; 5 is sufficient for PoC-scale catalogs

    Returns the same list with is_background and background_probability set.
    Events with background_probability >= 0.5 are classified as background.
    """
    if not events:
        return events
    if params is None:
        params = ETASParams()

    events = sorted(events, key=lambda e: e.time_days)
    n = len(events)
    phi = [1.0] * n  # initialise: all events assumed background

    for _ in range(n_iterations):
        new_phi: list[float] = []
        for i, ev_i in enumerate(events):
            bg = params.mu
            trig = 0.0
            for j in range(i):
                ev_j = events[j]
                if ev_j.magnitude < mc:
                    continue
                dt = ev_i.time_days - ev_j.time_days
                if dt <= 0.0:
                    continue
                kj = params.K * math.exp(params.alpha * (ev_j.magnitude - mc))
                trig += phi[j] * kj / (dt + params.c) ** params.p
            total = bg + trig
            new_phi.append(bg / total if total > 0.0 else 1.0)
        phi = new_phi

    for i, ev in enumerate(events):
        p_bg = round(phi[i], 4)
        ev.background_probability = p_bg
        ev.is_background = p_bg >= 0.5

    return events
