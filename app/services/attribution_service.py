import math
import logging
from app.schemas.analysis import AttributionResult, AttributionSignal, EventContextOut

log = logging.getLogger(__name__)

_ENGINE = "heuristic_v0"

# Exponential-decay length-scales (km). These are intentionally simple
# placeholders — the real Permian physics engine will replace this entire module.
_SWD_LAMBDA_KM = 10.0    # Smye 2024: pressure fronts migrate ~10 km/yr in Delaware Basin
_FRAC_LAMBDA_KM = 3.0    # Poroelastic stress changes decay sharply with distance


class HeuristicAttributionService:
    """Placeholder attribution engine. Scores SWD vs. frac drivers using distance-
    weighted cumulative injection / water volume. Replace with the Permian physics
    engine by swapping the dependency in app/api/dependencies.py — the response
    contract (AttributionResult) will remain the same."""

    def score(self, context: EventContextOut) -> AttributionResult:
        swd_score = self._swd_score(context)
        frac_score = self._frac_score(context)

        total = swd_score + frac_score
        if total == 0.0:
            driver = "indeterminate"
            confidence = 0.0
        elif swd_score >= frac_score:
            driver = "swd"
            confidence = (swd_score - frac_score) / swd_score if swd_score > 0 else 0.0
        else:
            driver = "frac"
            confidence = (frac_score - swd_score) / frac_score if frac_score > 0 else 0.0

        confidence = round(min(max(confidence, 0.0), 1.0), 4)

        signals: list[AttributionSignal] = []
        for w in context.nearby_swd_wells:
            if w.cumulative_bbl > 0:
                weight = math.exp(-w.distance_km / _SWD_LAMBDA_KM)
                signals.append(
                    AttributionSignal(
                        name=f"SWD {w.uic_number}",
                        value=round(w.cumulative_bbl * weight, 2),
                        unit="weighted_bbl",
                        description=(
                            f"{w.uic_number} — {w.distance_km} km away, "
                            f"{w.cumulative_bbl:,.0f} bbl cumulative in window"
                        ),
                    )
                )
        for j in context.nearby_frac_jobs:
            wv = j.total_water_volume or 0.0
            if wv > 0:
                weight = math.exp(-j.distance_km / _FRAC_LAMBDA_KM)
                signals.append(
                    AttributionSignal(
                        name=f"FRAC {j.api_number or j.well_name or 'unknown'}",
                        value=round(wv * weight, 2),
                        unit="weighted_gal",
                        description=(
                            f"Frac job at {j.distance_km} km, started {j.job_start_date}, "
                            f"{wv:,.0f} gal water volume"
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

    def _swd_score(self, context: EventContextOut) -> float:
        total = 0.0
        for w in context.nearby_swd_wells:
            if w.cumulative_bbl > 0 and w.distance_km > 0:
                total += w.cumulative_bbl * math.exp(-w.distance_km / _SWD_LAMBDA_KM)
        return total

    def _frac_score(self, context: EventContextOut) -> float:
        total = 0.0
        for j in context.nearby_frac_jobs:
            wv = j.total_water_volume or 0.0
            if wv > 0 and j.distance_km > 0:
                total += wv * math.exp(-j.distance_km / _FRAC_LAMBDA_KM)
        return total
