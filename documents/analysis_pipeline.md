# Analysis Pipeline — Event Context + Attribution

**Layer:** Analysis (reads across all ingestion buckets)
**Status:** IMPLEMENTED — heuristic v0 (placeholder for Permian physics engine)
**Endpoints:** `GET /api/v1/analysis/events/{event_id}/context`, `POST /api/v1/analysis/events/{event_id}/analyze`

---

## Purpose

This layer turns the four ingested data buckets (Seismic, SWD, Frac, IRIS Stations) into the core PoC workflow described in the SoW:

```
User selects seismic event → system assembles nearby context → attribution engine scores drivers
→ returns: likely driver + confidence + supporting signals + evidence object
```

It is the only layer a frontend needs to call. The two endpoints expose the full SoW contract.

---

## Architecture

```
GET  /api/v1/analysis/events/{event_id}/context   (read-only, no DB write)
POST /api/v1/analysis/events/{event_id}/analyze   (runs scorer + persists snapshot)

Both routes call:
  EventContextService.assemble()
    ├── SeismicEventRepository.get_by_event_id()        → event lat/lon/depth/date
    ├── SWDRepository.find_wells_in_bbox()
    │     + SWDRepository.get_monitoring_window()       → per-well H-10 timeseries summary
    ├── FracFocusRepository.find_nearby()               → frac jobs in bbox + time window
    └── IRISStationRepository.find_stations_in_bbox()   → nearby seismic stations

POST only:
  HeuristicAttributionService.score()                   → AttributionResult
  EventContextRepository.save_snapshot()                → event_context_snapshot table
```

---

## API Endpoints

### GET `/api/v1/analysis/events/{event_id}/context`

Assembles and returns the evidence object for a seismic event. Does **not** persist anything — safe to call repeatedly.

**Query parameters (all optional — defaults from Settings):**

| Parameter | Default | Description |
|---|---|---|
| `swd_radius_km` | 20.0 | SWD spatial search radius |
| `swd_window_days` | 3650 | SWD lookback (10 years — pressure fronts migrate slowly) |
| `frac_radius_km` | 10.0 | Frac spatial search radius |
| `frac_window_days` | 730 | Frac lookback (2 years — poroelastic stress is transient) |
| `station_radius_km` | 50.0 | IRIS station search radius |

**Returns:** `EventContextOut`

```json
{
  "event_id": "tx2026ihalbq",
  "event_latitude": 31.664,
  "event_longitude": -104.402,
  "event_depth_km": 4.6977,
  "event_date": "2026-04-28T03:16:56",
  "event_magnitude": 2.5,
  "swd_radius_km": 20,
  "swd_window_days": 3650,
  "frac_radius_km": 10,
  "frac_window_days": 730,
  "station_radius_km": 50,
  "nearby_swd_wells": [...],
  "nearby_frac_jobs": [...],
  "nearby_stations": [...]
}
```

**Returns 404** if `event_id` is not found. **Returns 422** if a radius param exceeds its allowed range.

---

### POST `/api/v1/analysis/events/{event_id}/analyze`

Assembles context + runs attribution + **persists a snapshot row** + returns the full result. Every call appends a new snapshot — prior runs are never mutated. Accepts the same query parameters as the GET endpoint.

**Returns:** `EventAnalysisOut`

```json
{
  "snapshot_id": 4,
  "context": { ... },
  "attribution": {
    "engine": "heuristic_v0",
    "likely_driver": "swd",
    "confidence": 0.82,
    "swd_score": 33499356.64,
    "frac_score": 4120000.0,
    "signals": [
      {
        "name": "SWD 000112977",
        "value": 6759614.3,
        "unit": "weighted_bbl",
        "description": "000112977 — 17.3 km away, 38,247,027 bbl cumulative in window"
      }
    ]
  }
}
```

---

## Spatial Search — How It Works

All three sources use the same two-step approach:

1. **Bounding box (SQL)** — filter rows by `lat ± pad` and `lon ± pad` where `pad = radius_km / 111`. Fast index scan; returns a superset.
2. **Haversine filter (Python)** — compute great-circle distance for each candidate; discard those outside `radius_km`. Exact.

The bounding box overcounts in the corners (~21% extra rows at most); haversine corrects it. This avoids pushing complex geometry into SQLite.

---

## SWD Context Fields

Each `NearbySWDWell` in the response carries:

| Field | Source | Meaning |
|---|---|---|
| `uic_number` | `swd_wells.uic_number` | RRC permit ID |
| `api_no` | `swd_wells.api_no` | API cross-join key |
| `distance_km` | computed | Haversine distance from event |
| `top_inj_zone` / `bot_inj_zone` | `swd_wells` | Injection zone depth (feet) — compare to event depth |
| `monthly_record_count` | H-10 join | Number of monthly reports in the time window |
| `cumulative_bbl` | H-10 join | Sum of `vol_liq` across window — primary attribution input |
| `avg_pressure_psi` | H-10 join | Mean injection pressure — Smye 2024 links high pressure to induced seismicity |
| `max_pressure_psi` | H-10 join | Peak recorded pressure in window |

**Wells with `monthly_record_count > 0` but `cumulative_bbl = 0`** are likely gas injection wells (reporting `vol_mcf` only, not `vol_liq`) or zero-volume permit-maintenance filings. They are not liquid SWD candidates.

---

## Frac Context Fields

Each `NearbyFracJob` carries:

| Field | FracFocus column | Meaning |
|---|---|---|
| `api_number` | `apinumber` | API well number |
| `distance_km` | computed | Haversine distance |
| `job_start_date` | `jobstartdate` | Completion start — key for temporal correlation |
| `job_end_date` | `jobenddate` | Completion end |
| `operator_name` | `operatorname` | Operator |
| `well_name` | `wellname` | Well name |
| `total_water_volume` | `totalbasewatervolume` | Gallons — primary frac intensity input |
| `formation_depth` | `tvd` | Total vertical depth (feet) — proxy for target formation depth |

**FracFocus column naming:** the bulk CSV is ingested with all headers lowercased and spaces removed (`CsvIngestionService.infer_columns()`). The actual column names in SQLite are `totalbasewatervolume` and `tvd` — **not** `totalwatervolume` or `formationdepth` as some data plan documents imply.

**FracFocus is a flattened table** — one row per chemical ingredient per job. `find_nearby()` deduplicates on `(apinumber, jobstartdate)` using `GROUP BY` to return one row per job. Date filtering is done in Python (not SQL) because FracFocus stores dates in US locale format (`M/D/YYYY H:MM:SS AM/PM`) which does not sort correctly as a plain string.

---

## Heuristic Attribution Engine — `heuristic_v0`

**This is a placeholder.** It exists to give the frontend and PoC demo a working end-to-end result while Travis Walla's Permian physics engine is integrated.

### Scoring formula

```
SWD score  = Σ  cumulative_bbl_i  × exp(−distance_km_i / 10)
Frac score = Σ  water_volume_j    × exp(−distance_km_j / 3)

driver     = whichever score is higher
confidence = |swd_score − frac_score| / max(swd_score, frac_score)
```

- **SWD λ = 10 km** — pore pressure fronts migrate ~10 km/yr in Delaware Basin (Smye et al. 2024)
- **Frac λ = 3 km** — poroelastic stress changes decay sharply with distance (Aziz Zanjani et al. 2024)
- **`indeterminate`** is returned when both scores are zero (no injection data in window)

### What `confidence` means in `heuristic_v0`

| Value | Meaning |
|---|---|
| `1.0` | Only one driver has data — the other is zero. **Not a high-confidence scientific claim.** |
| `0.7–0.9` | One driver clearly dominates, competing signal present |
| `0.3–0.6` | Both drivers active with similar weight |
| `0.0` | Both scores zero — indeterminate |

`confidence: 1.0` almost always means FracFocus returned no jobs in the window, not that SWD causation is certain. The real Permian engine produces a likelihood ratio with full Monte Carlo uncertainty quantification — that is the defensible number.

### What the heuristic ignores (and the physics engine does not)

- **Depth gap** — event depth vs. injection zone depth. A 4.7 km earthquake above wells injecting at 0.6–1.5 km requires vertical pressure diffusion; the heuristic ignores this entirely.
- **Formation properties** — hydraulic diffusivity, permeability, porosity. Controls how fast and far pressure propagates.
- **Fault geometry** — proximity to mapped Delaware Basin faults, fault slip tendency under current stress.
- **Pressure diffusion timescale** — whether 9.5 years of injection is enough for pressure to reach the fault at depth.
- **Reporting lag** — FracFocus has a 30–90 day disclosure lag; the heuristic does not model this uncertainty.

---

## Snapshot Table — `event_context_snapshot`

Every `POST /analyze` call appends one row. Rows are never updated or deleted — the table is an audit trail.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `event_id` | TEXT | Seismic event ID (value FK to `seismic_events.event_id`) |
| `run_timestamp` | DATETIME | UTC timestamp of this analysis run |
| `swd_radius_km` | FLOAT | Search radius used |
| `swd_window_days` | INTEGER | Lookback window used |
| `frac_radius_km` | FLOAT | Search radius used |
| `frac_window_days` | INTEGER | Lookback window used |
| `station_radius_km` | FLOAT | Search radius used |
| `engine` | TEXT | Attribution engine label (`"heuristic_v0"`) |
| `likely_driver` | TEXT | `"swd"` / `"frac"` / `"indeterminate"` |
| `confidence` | FLOAT | 0.0–1.0 (see caveats above) |
| `signals_json` | TEXT | JSON array of `AttributionSignal` objects |
| `nearby_swd_count` | INTEGER | Number of SWD wells found |
| `nearby_frac_count` | INTEGER | Number of frac jobs found |
| `nearby_station_count` | INTEGER | Number of IRIS stations found |

Unique constraint on `(event_id, run_timestamp)`. Re-running the same event with the same timestamp is not allowed; in practice timestamps differ by at least milliseconds.

Schema migrations follow the same `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` pattern as all other ORM tables. See `_ensure_event_context_columns()` in `app/core/database.py`.

---

## Integrating Travis's Physics Engine

The heuristic seam is in `app/api/dependencies.py`:

```python
def get_attribution_service() -> HeuristicAttributionService:
    return HeuristicAttributionService()
```

To swap in the real engine:

1. Create `app/services/physics_attribution_service.py` with a class implementing:
   ```python
   def score(self, context: EventContextOut) -> AttributionResult:
       ...
   ```
2. Change the factory above to return the new class.
3. Update the `engine` label string (e.g. `"permian_v1"`).

No endpoint, schema, or snapshot-table changes are needed. The `AttributionResult` fields (`engine`, `likely_driver`, `confidence`, `swd_score`, `frac_score`, `signals`) will be populated by the new engine.

**Key question to confirm with Travis before writing the adapter:** does his Theis pressure diffusion model need month-by-month H-10 records or just the cumulative total? The current `NearbySWDWell` carries only the aggregate (`cumulative_bbl`, `avg_pressure_psi`). If he needs the timeseries, the adapter should call `SWDRepository.get_monitoring_window()` directly rather than reading from the pre-aggregated context object.

---

## Key Files

| File | Role |
|---|---|
| `app/api/v1/endpoints/analysis.py` | FastAPI routes — GET context, POST analyze |
| `app/services/event_context_service.py` | Assembles spatial + temporal join across all buckets |
| `app/services/attribution_service.py` | Heuristic scorer — replace this to swap engine |
| `app/repositories/event_context_repository.py` | Reads/writes `event_context_snapshot` |
| `app/models/event_context.py` | ORM model for `event_context_snapshot` |
| `app/schemas/analysis.py` | Pydantic schemas: `EventContextOut`, `AttributionResult`, `EventAnalysisOut` |
| `app/utils/geo.py` | `haversine_km()` — great-circle distance helper |
| `app/api/dependencies.py` | DI wiring — `get_event_context_service`, `get_attribution_service` |

---

## Default Search Windows — Rationale

| Source | Radius | Window | Literature basis |
|---|---|---|---|
| SWD | 20 km | 10 years | Smye et al. 2024: pressure fronts migrate over years and >10 km in Delaware Basin |
| Frac | 10 km | 2 years | Aziz Zanjani et al. 2024: poroelastic stress changes are shorter-range and transient |
| IRIS stations | 50 km | n/a | Source-receiver geometry context; stations are static metadata |

All defaults are overridable per request via query parameters.
