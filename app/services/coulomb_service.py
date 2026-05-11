"""Coulomb Failure Function (CFF) contribution to attribution scoring.

Estimates whether injection-induced pore pressure brings a fault closer to
failure, providing a physics-based multiplier on top of the Theis diffusion
score.

Simplified plane-stress formulation (pressure-change term only):

    ΔCFF = ΔP × (sin²θ − μ' cosθ sinθ)

where
    ΔP  — pore pressure change at the fault (MPa)
    θ   — angle between fault strike and σ_Hmax direction
    μ'  — effective friction coefficient (Byerlee ≈ 0.6)

The full Coulomb formulation also includes an elastic stress change term
(Okada 1992), which requires fault geometry and elastic parameters beyond
what the PoC data provides. The pressure-change term dominates for
injection-induced seismicity and is sufficient for this stage.

Delaware Basin context
----------------------
σ_Hmax azimuth ≈ N45°E (World Stress Map; Zoback 2007, West Texas).
Optimally oriented faults for strike-slip failure: θ ≈ 15–30° from σ_Hmax.
CFF > 0 means the fault is brought closer to failure.

References
----------
Byerlee J. (1978). Friction of rocks. Pure and Applied Geophysics.
Zoback M. (2007). Reservoir Geomechanics. Cambridge University Press.
King G. et al. (1994). Static stress changes and the triggering of earthquakes.
"""

import math
import logging
from typing import Optional

log = logging.getLogger(__name__)

# Delaware Basin regional stress — σ_Hmax azimuth, degrees clockwise from North
_DELAWARE_SHMAX_AZ: float = 45.0

# Byerlee (1978) effective friction for faulted rock
_MU_PRIME: float = 0.6

# Pressure scale for normalisation (MPa).
# 1 MPa ≈ 145 psi — commonly cited critical threshold for induced seismicity.
_CFF_SCALE_MPA: float = 1.0

_PSI_PER_MPA: float = 145.038


def optimal_fault_angle(shmax_az_deg: float = _DELAWARE_SHMAX_AZ) -> float:
    """Mohr-Coulomb optimal fault angle from σ_Hmax for strike-slip failure.

    θ_opt = 45° − arctan(μ') / 2  ≈ 29.5° for μ' = 0.6
    """
    return 45.0 - math.degrees(math.atan(_MU_PRIME)) / 2.0


def cff_weight(
    delta_p_mpa: float,
    fault_strike_deg: Optional[float] = None,
    shmax_az_deg: float = _DELAWARE_SHMAX_AZ,
    mu_prime: float = _MU_PRIME,
) -> float:
    """Coulomb failure weight ∈ [0, 1].

    Pore pressure increases CFF by reducing effective normal stress
    (Terzaghi effective-stress principle):

        ΔCFF_pore = μ' × ΔP  (always positive for ΔP > 0)

    An orientation factor f ∈ [0.5, 1.0] modulates the result: f = 1.0 for
    optimally oriented faults, f = 0.5 for fault orientation unknown. The
    factor is computed as the cosine similarity between the fault angle and
    the Mohr-Coulomb optimal angle (θ_opt = 45° − arctan(μ') / 2 ≈ 29.5°).

    Parameters
    ----------
    delta_p_mpa      : pore pressure change at the fault (MPa)
    fault_strike_deg : fault strike (degrees from N, clockwise); when None
                       a moderate orientation factor (0.75) is assumed
    shmax_az_deg     : σ_Hmax azimuth (default: Delaware Basin N45°E)
    mu_prime         : effective friction coefficient

    Returns 0.0 when ΔP ≤ 0. Approaches 1.0 as μ'ΔP / scale increases.
    """
    if delta_p_mpa <= 0.0:
        return 0.0

    # Base pore-pressure CFF contribution — independent of fault orientation
    base_cff = mu_prime * delta_p_mpa

    # Orientation factor
    if fault_strike_deg is None:
        orientation_f = 0.75  # conservative estimate for unknown fault geometry
    else:
        theta_deg = abs(fault_strike_deg - shmax_az_deg) % 90.0
        theta_opt = optimal_fault_angle(shmax_az_deg)
        # 1.0 at optimal angle, decays smoothly toward 0.5 at 0° or 90°
        angle_diff = abs(theta_deg - theta_opt)
        orientation_f = 0.5 + 0.5 * math.cos(math.radians(angle_diff / theta_opt * 90.0))

    cff = base_cff * orientation_f

    # Exponential saturation: cff = _CFF_SCALE_MPA → weight ≈ 0.63
    return round(min(1.0 - math.exp(-cff / _CFF_SCALE_MPA), 1.0), 4)


def psi_to_mpa(pressure_psi: float) -> float:
    """Convert psi to MPa."""
    return pressure_psi / _PSI_PER_MPA


def cff_weight_from_psi(
    avg_pressure_psi: Optional[float],
    fault_strike_deg: Optional[float] = None,
) -> float:
    """Convenience wrapper — takes avg_pressure_psi directly from H-10 records.

    Returns 0.0 when pressure is None or non-positive.
    """
    if avg_pressure_psi is None or avg_pressure_psi <= 0.0:
        return 0.0
    return cff_weight(psi_to_mpa(avg_pressure_psi), fault_strike_deg=fault_strike_deg)
