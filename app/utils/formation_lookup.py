"""Formation-specific hydraulic diffusivity for the Delaware Basin.

Maps injection zone depth (TVD in feet) to hydraulic diffusivity D (m²/s)
using Delaware Basin stratigraphy and published permeability/diffusivity
estimates from induced seismicity literature.

Sources
-------
Smye K. et al. (2024). Hydraulic diffusivity in the Delaware Basin from
    SWD-induced seismicity migration rates.
Frohlich C. et al. (2016). Deep earthquakes and fluid injection in the
    Permian Basin of West Texas.
Goebel T. et al. (2017). Wastewater disposal and induced seismicity
    in the Delaware Basin.
"""

from dataclasses import dataclass
from typing import Optional

_DEFAULT_D: float = 0.5  # m²/s — conservative midrange (Smye 2024 range 0.1–1.0)
_DELAWARE_DEFAULT_DEPTH_FT: float = 7_500.0  # Wolfcamp A / Bone Spring midpoint


@dataclass(frozen=True)
class _Formation:
    name: str
    top_ft: float
    base_ft: float
    d_m2_s: float  # geometric mean from published Delaware Basin studies


# Delaware Basin stratigraphic column (Culberson / Reeves / Ward / Loving / Pecos).
# Depths are representative formation tops; calibrate with local well data.
_FORMATIONS: list[_Formation] = [
    _Formation("Rustler / Salado Evaporites",  0,       3_500,  0.001),
    _Formation("Delaware Sand",                3_500,   6_500,  0.15),
    _Formation("Bell Canyon",                  6_500,   7_500,  0.20),
    _Formation("Cherry Canyon",                7_500,   8_500,  0.18),
    _Formation("Brushy Canyon",                8_500,   9_200,  0.22),
    _Formation("Bone Spring 1st",              9_200,  10_000,  0.60),
    _Formation("Bone Spring 2nd",             10_000,  10_700,  0.50),
    _Formation("Bone Spring 3rd",             10_700,  11_400,  0.45),
    _Formation("Wolfcamp A",                  11_400,  12_400,  0.35),
    _Formation("Wolfcamp B",                  12_400,  13_300,  0.25),
    _Formation("Wolfcamp C",                  13_300,  14_200,  0.15),
    _Formation("Wolfcamp D",                  14_200,  15_000,  0.10),
    _Formation("Pennsylvanian",               15_000,  16_500,  0.08),
    _Formation("Basement",                    16_500, 100_000,  0.03),
]


def get_diffusivity(top_ft: Optional[float], bot_ft: Optional[float] = None) -> float:
    """Return hydraulic diffusivity D (m²/s) for the given injection zone depth.

    Uses midpoint of [top_ft, bot_ft] when both are provided, otherwise top_ft alone.
    Returns _DEFAULT_D when depth is None or falls outside the stratigraphic table.
    """
    if top_ft is None:
        return _DEFAULT_D
    depth_ft = top_ft if bot_ft is None else (top_ft + bot_ft) / 2.0
    for f in _FORMATIONS:
        if f.top_ft <= depth_ft < f.base_ft:
            return f.d_m2_s
    return _DEFAULT_D


def get_formation_name(top_ft: Optional[float], bot_ft: Optional[float] = None) -> str:
    """Return the formation name for the given depth, or 'Unknown'."""
    if top_ft is None:
        return "Unknown"
    depth_ft = top_ft if bot_ft is None else (top_ft + bot_ft) / 2.0
    for f in _FORMATIONS:
        if f.top_ft <= depth_ft < f.base_ft:
            return f.name
    return "Unknown"
