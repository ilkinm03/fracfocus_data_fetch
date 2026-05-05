# Delaware Basin Seismic Attribution Platform — Project Overview

> **What this is, how it works, and why it matters.**

---

## The Big Picture

Deep in the Delaware Basin — a geological formation stretching across West Texas and southeastern New Mexico — two industries operate side by side: hydraulic fracturing ("fracking") and saltwater disposal (SWD). Both inject fluids deep underground. Both, under certain conditions, can trigger earthquakes.

This platform answers one question: **When an earthquake happens in the Delaware Basin, was it caused by nearby injection activity — and if so, which kind?**

To answer that, it pulls data from five different government and research databases, unifies everything in a single local database, and runs a physics-informed scoring algorithm that weighs every nearby injection well and fracking job by distance and volume. The result is an attribution verdict — *SWD*, *frac*, or *indeterminate* — along with a confidence score and a ranked list of the most likely contributors.

---

## A Quick Tour of the Data

Before diving into the machinery, it helps to know what data the system works with.

| What | Where it comes from | What it contains |
|---|---|---|
| **Seismic events** | TexNet (Univ. of Texas) | Earthquakes detected in the Delaware Basin since TexNet's deployment |
| **Seismic events** | USGS FDSN catalog | Historical earthquakes going back to 2000, covering gaps in TexNet's early years |
| **Seismic stations** | EarthScope / IRIS | The monitoring stations that *detect* earthquakes — useful for understanding coverage gaps |
| **SWD wells** | Texas RRC UIC inventory | Every permitted saltwater disposal well: location, depth, injection zone |
| **SWD monthly injection** | Texas RRC H-10 reports | Month-by-month injection volumes and pressures for every active well |
| **Hydraulic fracturing jobs** | FracFocus bulk download | Every disclosed fracking job: location, water volume used, dates, operator |

All of this lands in a single SQLite database file. No cloud services, no external dependencies at query time — everything runs locally.

---

## The Three Buckets

The system organizes its work into three conceptual buckets:

```
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│   SEISMIC   │   │     SWD     │   │    FRAC     │
│             │   │             │   │             │
│  TexNet     │   │  UIC wells  │   │  FracFocus  │
│  USGS       │   │  H-10 data  │   │  disclos.   │
│  IRIS stn.  │   │             │   │             │
└─────────────┘   └─────────────┘   └─────────────┘
        │                │                 │
        └────────────────┴─────────────────┘
                         │
                 ┌───────▼────────┐
                 │  SQLite DB     │
                 │  (single file) │
                 └───────┬────────┘
                         │
                ┌────────▼─────────┐
                │  Analysis Layer  │
                │  (attribution)   │
                └──────────────────┘
```

---

## How Data Gets In

Each source has its own ingestion pipeline. None of them interfere with each other — they all write to the same database through separate paths.

### Seismic Events (TexNet)

TexNet exposes its earthquake catalog through an ArcGIS REST service. The system queries it for all events within the Delaware Basin bounding box (`28.5°–32.5° N`, `105.5°–102.5° W`). Each event is stored with its magnitude, location, depth, and evaluation status. TexNet assigns earthquakes a status of `"final"` or `"preliminary"` depending on how thoroughly the location has been refined.

### Seismic Events (USGS)

The USGS FDSN (Federated Digital Seismic Network) API covers the same region but goes back further in time. A critical detail: if you query USGS without specifying a start date, it silently returns only the last 30 days of events. The system always sends `starttime=2000-01-01` to get full historical coverage. USGS events are tagged with `source="usgs"` and live in the same `seismic_events` table as TexNet events.

USGS also provides `alternate_ids` — a comma-separated list of every network ID that refers to the same physical earthquake. This allows cross-referencing a TexNet event with its USGS counterpart.

### Seismic Stations (IRIS)

EarthScope's IRIS service provides metadata about the seismograph stations themselves. This doesn't detect earthquakes — it tells you *where the detectors are*. This matters for understanding whether an area has good seismic coverage. A cluster of events near a station may be well-located; an event far from any station might have a poorly constrained epicenter.

### SWD Wells (UIC Inventory)

The Texas Railroad Commission publishes its Underground Injection Control (UIC) well inventory through the Texas Open Data Portal (Socrata API). The system fetches every well record — location, API number, injection zone depths, pressure limits — and stores it in `swd_wells`. This is a large dataset, so the fetch is **checkpoint-resumable**: if interrupted, it picks up where it left off.

### SWD Monthly Injection (H-10 Reports)

Once wells are in the database, their monthly injection records are fetched from the H-10 monitoring dataset — also through Socrata. For each well, the system retrieves every month's average injection pressure, maximum pressure, and total liquid volume injected. These are the numbers that feed directly into the attribution formula.

### Hydraulic Fracturing Jobs (FracFocus)

FracFocus publishes bulk downloads of fracking disclosures as a ZIP file containing many CSVs. The system downloads the ZIP, checks whether each CSV has changed since the last sync (by comparing file metadata without decompressing), and only re-ingests CSVs that actually changed. The FracFocus schema is inferred dynamically from CSV column headers and normalized to lowercase-with-underscores (e.g., `TotalBaseWaterVolume` → `totalbasewatervolume`).

FracFocus is the only source that runs on a schedule — automatically on the first day of each month at 2:00 AM UTC. All seismic and SWD fetches are manual.

---

## The Analysis Pipeline

This is where the system moves from raw data to insight.

### Step 1 — Assemble Context

When you ask the system to analyze a seismic event, it first assembles everything it knows about the area around that event during a relevant time window:

**SWD context** (default: 20 km radius, 10-year lookback)
- Every nearby injection well
- Its monthly injection history during the lookback period
- Aggregated: cumulative barrels injected, average pressure, maximum pressure

**Frac context** (default: 10 km radius, 2-year lookback)
- Every nearby hydraulic fracturing job completed before the earthquake
- Total water volume used, operator name, formation depth, job dates

**Station context** (default: 50 km radius)
- Nearby seismic monitoring stations
- Operational status (active/inactive during the event)

The different radii and windows reflect the underlying physics. Pressure fronts from saltwater disposal wells migrate slowly outward — they can reach 20 km or more over years. Fracking-induced stress is more localized and fades faster, so a tighter 10 km window is appropriate.

### Step 2 — Score Attribution

The attribution model uses an **exponential decay** approach: nearby injectors count more than distant ones, and the decay rate is calibrated to the geophysical process.

**SWD Score formula:**
```
SWD_Score = Σ  cumulative_bbl[i]
               × exp( −distance_km[i] / 10 )
               × exp( −days_since_last_report[i] / 365 )
               × exp( −Δdepth_km[i]² / (2 × 3²) )
               × min( rate_change_ratio[i], 3.0 )
              i ∈ nearby_swd_wells
```

**Frac Score formula:**
```
Frac_Score = Σ  (water_volume_gallons[i] / 42)
               × exp( −distance_km[i] / 3 )
               × exp( −Δdepth_km[i]² / (2 × 3²) )
               i ∈ nearby_frac_jobs
```

The division by 42 converts gallons to barrels so both scores are in comparable units. The decay constants encode physical intuition:

- **λ_space = 10 km (SWD):** Pore pressure from disposal wells diffuses gradually. A well 10 km away still contributes meaningfully over a 10-year period. This value is informed by Smye (2024) and similar pore-pressure diffusion studies.
- **λ_time = 365 days (SWD):** Pressure dissipates after injection stops. A well whose last H-10 record is 365 days before the earthquake contributes ~37% of what it would if it were still actively injecting; at 730 days (~2 years), that falls to ~14%. If no `last_report_date` is available the temporal factor defaults to 1.0 (no penalty).
- **λ = 3 km (frac):** Poroelastic stress changes from fracking are sharp and short-range. By 3 km, the contribution has fallen to ~37% of what it would be at the wellbore; by 9 km, it's under 5%.
- **σ = 3 km (depth mismatch, both sources):** A Gaussian penalty applied when the earthquake depth does not match the injection zone depth. RRC and FracFocus report depths in feet; the engine converts to km before computing the delta against the seismically-determined hypocenter depth. At Δdepth = 3 km the weight is ~0.61; at 6 km it is ~0.14; at 9 km it is ~0.01. If depth data is absent for either the event or the well/frac job, the factor defaults to 1.0 (no penalty). For frac jobs, which have a single TVD rather than a zone range, the TVD is used directly as the reference depth.
- **rate_change_ratio, capped at 3× (SWD only):** The ratio of mean monthly injection volume in the 3 months immediately before the earthquake versus the prior 9 months. A well that doubled its injection rate in the 3 months before the event gets a ×2.0 boost; one that tripled or more is capped at ×3.0. A ratio below 1.0 (injection declining) reduces the score proportionally. The ratio is computed only when ≥4 H-10 records exist in the window and the prior-period average is non-zero; otherwise the factor defaults to 1.0. The raw ratio and the applied (capped) value both appear in the signal description.

**Determining the driver:**

```
p_swd = SWD_Score / (SWD_Score + Frac_Score)   # proportion of total score attributed to SWD

if   p_swd > 0.5:  driver = "swd",           confidence = p_swd
elif p_swd < 0.5:  driver = "frac",          confidence = 1 − p_swd
elif total == 0:   driver = "indeterminate",  confidence = 0.0
else:              driver = "indeterminate",  confidence = 0.5   # exact tie, non-zero evidence
```

`p_swd` is the softmax of the two scores — mathematically equivalent to `sigmoid(log-odds)`. Confidence is directly interpretable as the probability that the identified driver is correct: **0.5 means a coin-flip, 1.0 means the evidence is entirely one-sided**. It can never fall below 0.5 when a driver is named (a score that low would flip the driver label instead). The only time confidence is 0.0 is when there is no evidence at all — no nearby wells and no nearby frac jobs within the search windows.

**Signals:** Along with the overall verdict, the system produces a ranked list of contributing sites — each well or frac job that influenced the score, listed from most to least influential. When frac data is absent, the Monte Carlo estimate (described below) appears in the signal list as `"FRAC [MC estimate]"` with its mean, p5, and p95 values.

**Monte Carlo frac uncertainty (when `nearby_frac_jobs` is empty):**

FracFocus has structural underreporting — not every operator files disclosures, especially smaller independents. When the search returns zero frac jobs, setting `frac_score = 0` is epistemically incorrect: it produces `confidence = 1.0` for SWD regardless of the SWD evidence, which overstates certainty.

To correct this, when no observed frac jobs are found the system:

1. **Builds a prior** by querying FracFocus in a 5× wider radius to fit log-normal water-volume and normal depth distributions to actual Delaware Basin completions. Falls back to published defaults (Wolfcamp/Bone Spring median ≈ 12.5 M gal/job, TVD ≈ 7,500 ft) when the broader query is also sparse.

2. **Runs 1,000 Monte Carlo trials**, each sampling:
   - Number of hypothetical jobs from Poisson(λ), where λ is scaled from the broader-area count to the search area
   - Distance from the event: area-weighted uniform on a disk (`r = R × √U`)
   - Water volume: log-normal from the fitted prior
   - TVD depth: normal from the fitted prior
   
3. **Computes frac_score per trial** using the same spatial-decay + depth-penalty formula as observed jobs.

4. **Reports `(mean, p5, p95)`** of the resulting distribution as new fields on `AttributionResult` — alongside an MC-adjusted verdict that uses `mc_frac_score_mean` instead of zero in the softmax.

The primary `likely_driver` and `confidence` fields are **not changed** — they continue to reflect the observed data. The `adjusted_likely_driver` and `adjusted_confidence` fields represent the MC-corrected interpretation:

```
# Example: strong SWD evidence, no observed frac data
likely_driver:          "swd"    confidence: 1.0    ← raw observed-data verdict (was misleading)
adjusted_likely_driver: "swd"    adjusted_confidence: 0.76   ← honest: MC frac is non-zero
frac_data_quality:      "absent"
mc_frac_score_mean:  55000   mc_frac_score_p5: 0   mc_frac_score_p95: 190000
```

When frac jobs *are* observed, `frac_data_quality = "observed"` and all `mc_*` and `adjusted_*` fields are `null`.

### Step 3 — Persist the Snapshot

Every analysis run is saved to the `event_context_snapshot` table. A snapshot records:
- Which event was analyzed
- When the analysis ran
- Which search parameters were used (radii, time windows)
- The attribution verdict and confidence
- The count of nearby SWD wells, frac jobs, and stations found
- MC frac uncertainty fields (`frac_data_quality`, `mc_frac_score_mean/p5/p95`, `adjusted_likely_driver`, `adjusted_confidence`) when frac data is absent

Snapshots are never overwritten — each analysis run adds a new row. This means you can re-run an analysis with different parameters and compare results side by side.

---

## The API

The platform exposes a REST API at `http://localhost:8000`. Here's a guided tour.

### Ingestion Endpoints

| Endpoint | What it does |
|---|---|
| `POST /api/v1/sync/trigger` | Download and ingest the FracFocus bulk ZIP |
| `POST /api/v1/seismic/texnet/fetch` | Fetch TexNet earthquake catalog |
| `POST /api/v1/seismic/usgs/fetch` | Fetch USGS earthquake catalog |
| `POST /api/v1/seismic/iris/stations/fetch` | Fetch IRIS seismic station metadata |
| `POST /api/v1/swd/uic/fetch` | Fetch UIC injection well inventory |
| `POST /api/v1/swd/h10/fetch` | Fetch H-10 monthly injection records |

All fetches return a result summary: how many records were fetched, inserted, and updated.

### Query Endpoints

| Endpoint | What it does |
|---|---|
| `GET /api/v1/seismic/events` | List earthquakes; filter by source, county, minimum magnitude |
| `GET /api/v1/seismic/iris/stations` | List seismic stations; filter by network, active status |
| `GET /api/v1/swd/wells` | List SWD injection wells |
| `GET /api/v1/swd/monitoring` | List monthly H-10 injection records |
| `GET /api/v1/data/` | List FracFocus fracking disclosures; filter by state or operator |
| `GET /api/v1/data/stats` | Total FracFocus record count |
| `GET /api/v1/data/columns` | All column names in the FracFocus table |
| `GET /api/v1/data/distinct/{column}` | All distinct values for a column |
| `GET /api/v1/data/group/{column}` | Distinct values with counts, sorted by frequency |

All list endpoints support pagination.

### Analysis Endpoints

| Endpoint | What it does |
|---|---|
| `GET /api/v1/analysis/events/{event_id}/context` | Assemble and return nearby context (read-only, no snapshot saved) |
| `POST /api/v1/analysis/events/{event_id}/analyze` | Assemble context, run attribution, save snapshot, return everything |

Both endpoints accept optional query parameters to override the default search radii and time windows:
- `swd_radius_km`, `swd_window_days`
- `frac_radius_km`, `frac_window_days`
- `station_radius_km`

### Sample Attribution Response — frac data observed

```json
{
  "snapshot_id": 42,
  "context": {
    "event_id": "tx2025iqwk",
    "event_magnitude": 3.2,
    "event_latitude": 31.847,
    "event_longitude": -103.921,
    "event_depth_km": 4.5,
    "event_date": "2025-06-15T14:23:00",
    "nearby_swd_wells": [{ "uic_number": "UIC-12345", "distance_km": 4.2, "cumulative_bbl": 1850000 }],
    "nearby_frac_jobs": [{ "api_number": "42-123-45678", "distance_km": 2.1, "total_water_volume": 14000000 }],
    "nearby_stations": [...],
    "frac_prior_params": null
  },
  "attribution": {
    "engine": "physics_v1",
    "likely_driver": "swd",
    "confidence": 0.8734,
    "swd_score": 142300.5,
    "frac_score": 18040.2,
    "frac_data_quality": "observed",
    "mc_frac_score_mean": null,
    "mc_frac_score_p5": null,
    "mc_frac_score_p95": null,
    "adjusted_likely_driver": null,
    "adjusted_confidence": null,
    "signals": [
      { "name": "SWD UIC-12345", "value": 98432.1, "unit": "weighted_bbl",
        "description": "UIC-12345 — 4.2 km away, 1,850,000 bbl cumul., pressure front ≈22.3 km, erfc=0.6120" },
      { "name": "FRAC 42-123-45678", "value": 17984.3, "unit": "weighted_bbl",
        "description": "Frac job at 2.1 km, started 2024-03-01, 14,000,000 gal (333,333 bbl) water volume" }
    ]
  }
}
```

### Sample Attribution Response — frac data absent (MC activated)

```json
{
  "snapshot_id": 43,
  "context": {
    "event_id": "tx2025abcd",
    "event_magnitude": 2.8,
    "event_latitude": 31.912,
    "event_longitude": -104.103,
    "event_depth_km": 5.2,
    "event_date": "2025-08-20T09:11:00",
    "nearby_swd_wells": [{ "uic_number": "UIC-67890", "distance_km": 6.8, "cumulative_bbl": 720000 }],
    "nearby_frac_jobs": [],
    "nearby_stations": [...],
    "frac_prior_params": {
      "source": "data_driven",
      "sample_size": 134,
      "n_jobs_mean": 2.1,
      "water_vol_log_mean": 12.7,
      "water_vol_log_std": 0.76,
      "depth_mean_ft": 7820.0,
      "depth_std_ft": 1340.0
    }
  },
  "attribution": {
    "engine": "physics_v1",
    "likely_driver": "swd",
    "confidence": 1.0,
    "swd_score": 44100.0,
    "frac_score": 0.0,
    "frac_data_quality": "absent",
    "mc_frac_score_mean": 41800.0,
    "mc_frac_score_p5": 0.0,
    "mc_frac_score_p95": 148200.0,
    "adjusted_likely_driver": "swd",
    "adjusted_confidence": 0.7134,
    "signals": [
      { "name": "SWD UIC-67890", "value": 44100.0, "unit": "weighted_bbl",
        "description": "UIC-67890 — 6.8 km away, 720,000 bbl cumul., pressure front ≈18.1 km, erfc=0.4820" },
      { "name": "FRAC [MC estimate]", "value": 41800.0, "unit": "weighted_bbl",
        "description": "Monte Carlo frac estimate (N=1000): mean=41800, p5=0, p95=148200 — prior source=data_driven, sample_size=134, n_jobs_mean=2.10" }
    ]
  }
}
```

---

## The Database Schema

All data lives in a single SQLite file (`fracfocus_data/fracfocus.db`). Here are the tables:

### `seismic_events`
Earthquake records from TexNet and USGS. Both sources write here, distinguished by the `source` column. Columns unique to one source are left null for the other (e.g., `county_name` is TexNet-only; `alternate_ids` is USGS-only).

### `swd_wells`
Static inventory of every UIC injection well: location, API number, operator, injection zone depths, pressure limits. Updated by the UIC fetch.

### `swd_monthly_monitor`
One row per well per month: injection pressure (average and max), liquid volume in barrels. The H-10 fetch populates this. Each `(uic_no, report_date)` pair is unique — a re-fetch updates the existing record. Records are returned ordered by `report_date` ascending; the last entry's date is surfaced as `last_report_date` in `NearbySWDWell` and used by the attribution engine for temporal decay.

### `iris_stations`
Seismic monitoring station metadata: network code, station code, location, elevation, and operational dates. A null `end_time` means the station is currently active.

### `fracfocus` (dynamic schema)
Hydraulic fracturing disclosures. The schema is inferred from the CSV headers at ingest time — no fixed column list. Column names are normalized to lowercase-no-spaces. Key columns used in analysis: `latitude`, `longitude`, `jobstartdate`, `totalbasewatervolume`, `apinumber`, `operatorname`.

### `event_context_snapshot`
Persisted analysis results. Every `POST /analyze` call appends a row — existing snapshots are never mutated. Stores the attribution verdict, confidence, signal JSON, search parameters used, counts of nearby features found, and six MC frac uncertainty columns: `frac_data_quality`, `mc_frac_score_mean`, `mc_frac_score_p5`, `mc_frac_score_p95`, `adjusted_likely_driver`, `adjusted_confidence`. The MC columns are `NULL` when frac data was observed.

### `swd_fetch_checkpoint`
Internal checkpoint table for resumable SWD fetches. Stores the current page offset and running insert/update counts per source. If a fetch is interrupted, it reads this table and continues from where it stopped.

### `sync_history`
Audit trail of every data fetch ever run. Stores the source, status (pending / running / success / failed / skipped), timestamps, and row counts. Useful for debugging and for confirming that data is current.

---

## How the Code Is Organized

```
app/
├── api/
│   ├── dependencies.py          ← Dependency injection wiring for all services
│   └── v1/endpoints/
│       ├── seismic.py           ← TexNet, USGS, IRIS endpoints
│       ├── swd.py               ← UIC, H-10 endpoints
│       ├── fracfocus.py         ← FracFocus query endpoints
│       └── analysis.py          ← Context assembly + attribution endpoints
├── core/
│   ├── config.py                ← All configuration via pydantic-settings + .env
│   └── database.py              ← Engine setup, init_db(), schema migration helpers
├── models/
│   ├── seismic_event.py         ← SeismicEvent ORM model
│   ├── swd_well.py              ← SWDWell, SWDMonthlyMonitor, SWDFetchCheckpoint ORM models
│   ├── iris_station.py          ← IRISStation ORM model
│   └── event_context_snapshot.py← EventContextSnapshot ORM model
├── repositories/
│   ├── seismic_event_repository.py
│   ├── swd_repository.py
│   ├── iris_station_repository.py
│   ├── fracfocus_repository.py  ← Uses SQLAlchemy Core (dynamic schema)
│   └── event_context_repository.py
├── services/
│   ├── texnet_service.py        ← ArcGIS REST client
│   ├── usgs_service.py          ← USGS FDSN GeoJSON client
│   ├── iris_service.py          ← EarthScope FDSN Station client
│   ├── uic_service.py           ← Socrata UIC client
│   ├── h10_service.py           ← Socrata H-10 client
│   ├── fracfocus_sync_service.py← ZIP download + CSV ingestion
│   ├── event_context_service.py ← Context assembly (builds MC prior when frac absent)
│   ├── attribution_service.py   ← Heuristic attribution engine
│   ├── physics_attribution_service.py ← Physics engine with MC uncertainty layer
│   └── mc_frac_prior.py         ← Monte Carlo frac prior: fitting + sampling
└── schemas/
    ├── seismic.py               ← Pydantic response models for seismic endpoints
    ├── swd.py                   ← Pydantic response models for SWD endpoints
    └── analysis.py              ← EventContextOut, AttributionResult, EventAnalysisOut
```

Two different SQLAlchemy paradigms coexist deliberately:
- **ORM models** (`SeismicEvent`, `SWDWell`, etc.) for tables with fixed, known schemas
- **SQLAlchemy Core** (`fracfocus` table) because the schema is inferred from CSV headers at runtime and cannot be expressed as a fixed Python class

---

## Schema Evolution — Adding Columns Without Losing Data

SQLite's `CREATE TABLE IF NOT EXISTS` creates tables but never modifies existing ones. The system handles schema evolution through a custom migration function: `_ensure_seismic_columns()` (and equivalents for other tables). On every startup, it:

1. Runs `PRAGMA table_info(<table>)` to see what columns exist
2. Compares against the ORM model
3. Runs `ALTER TABLE ADD COLUMN` for any missing columns

This is how the USGS-specific columns (`place`, `title`, `alternate_ids`, `gap`) were added to an already-populated `seismic_events` table without data loss. Any future column additions should follow this pattern.

---

## Configuration

All settings live in a `.env` file and are loaded once at startup via `pydantic-settings`. The most important ones:

| Setting | Default | Why it matters |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./fracfocus_data/fracfocus.db` | Path to the SQLite file |
| `USGS_START_TIME` | `2000-01-01` | Without this, USGS returns only the last 30 days |
| `ANALYSIS_SWD_RADIUS_KM` | `20.0` | SWD pressure fronts can travel this far |
| `ANALYSIS_SWD_WINDOW_DAYS` | `3650` | 10-year lookback — pressure diffusion is slow |
| `ANALYSIS_FRAC_RADIUS_KM` | `10.0` | Frac-induced stress is more localized |
| `ANALYSIS_FRAC_WINDOW_DAYS` | `730` | 2-year lookback for frac jobs |
| `ANALYSIS_STATION_RADIUS_KM` | `50.0` | Station coverage context radius |
| `SOCRATA_APP_TOKEN` | `""` | Optional; removes Socrata rate limits |
| `SYNC_ENABLED` | `true` | Disables the monthly FracFocus cron when false |

---

## The Attribution Engine — Current State and Future Path

The active engine is `physics_v1`. The engine label is stored on every `event_context_snapshot` row, so historical analyses remain correctly tagged even after the engine is upgraded. The heuristic engine (`heuristic_v4`) is still available and can be restored by editing `app/api/dependencies.py`.

### Version history

#### `heuristic_v0` — initial implementation
**Formula (SWD):** `cumulative_bbl × exp(−d / 10)`  
**Formula (frac):** `(water_vol_gal / 42) × exp(−d / 3)`

Pure distance-weighted volume. A well that last injected nine years ago scores identically to one that injected last month at the same distance. No depth awareness — a well perforated at 5,000 ft depth scores the same against a 1 km event as against a 6 km event.

---

#### `heuristic_v1` — temporal decay added to SWD
**Formula (SWD):** `cumulative_bbl × exp(−d / 10) × exp(−days_since_last_report / 365)`  
**Formula (frac):** unchanged from v0

Added `last_report_date` to `NearbySWDWell`. The temporal decay constant λ = 365 days reflects that pore pressure dissipates after injection stops: a well shut in for one full year contributes ~37% of its active-injecting score; at two years that falls to ~14%. Wells with no H-10 data in the window default to a temporal weight of 1.0 (no penalty applied).

**What changed in practice:** dormant wells no longer dominate the SWD score over actively injecting neighbors of similar volume.

---

#### `heuristic_v2` — depth mismatch penalty added to both sources *(current)*
**Formula (SWD):** `cumulative_bbl × exp(−d / 10) × exp(−days / 365) × exp(−Δdepth² / 18)`  
**Formula (frac):** `(water_vol_gal / 42) × exp(−d / 3) × exp(−Δdepth² / 18)`

Added a Gaussian depth-mismatch penalty (σ = 3 km) to both SWD and frac scoring. The penalty compares the seismic hypocenter depth (km, from USGS/TexNet) against the midpoint of the injection zone (converted from feet — RRC and FracFocus both report depths in feet). At Δdepth = 3 km the weight is ~0.61; at 6 km it is ~0.14; at 9 km it is ~0.01. If depth data is missing on either side, the factor defaults to 1.0. For frac jobs, which carry a single TVD rather than a zone range, TVD is used directly as the reference depth.

**What changed in practice:** wells perforated in a shallow formation no longer score highly against deep earthquakes, and vice versa. The depth note (`depth Δ{n} km`) now appears in every signal description.

---

#### `heuristic_v3` — log-odds confidence metric *(current)*
**Confidence formula:** `p_swd = SWD_Score / (SWD_Score + Frac_Score)`

Replaced the old `(winner − loser) / winner` ratio with a softmax (log-odds) formulation. The new `confidence` value is directly interpretable as the probability that the named driver is correct. It ranges from 0.5 (coin-flip — scores are equal) to 1.0 (fully one-sided). Under the old formula, scores of 142,000 vs. 18,000 and 8.7 vs. 1.1 both produced 0.87 — an identical-looking result despite very different evidence. Under the new formula, confidence encodes the *relative* split, not an inflated ratio.

The `indeterminate` label is now reserved for two cases only: total score is zero (no evidence found), or scores are exactly equal with non-zero evidence (genuine tie).

**What changed in practice:** confidence is now comparable across events. A result of 0.88 means SWD accounts for 88% of the total weighted signal, regardless of the absolute magnitude of the scores. A result near 0.5 is a genuine warning that the evidence is ambiguous.

---

#### `heuristic_v4` — injection rate change boost *(current)*
**New factor (SWD):** `× min( mean_vol_last_3_months / mean_vol_prior_9_months, 3.0 )`

Added `rate_change_ratio` to `NearbySWDWell`. For each well, the engine computes the ratio of mean monthly injection volume in the 3 months immediately before the earthquake against the preceding 9 months. This ratio is then used as a multiplicative score boost, capped at 3× to prevent a single anomalous month from dominating.

A well that ramped up from 5,000 bbl/month to 15,000 bbl/month (ratio = 3.0) now scores up to 3× higher than a well that held steady at the same cumulative total — reflecting the well-established empirical finding that injection rate acceleration is a stronger predictor of induced seismicity than injection volume alone.

Defaults to 1.0 (neutral) when fewer than 4 H-10 records exist in the window, or when the prior-period average is zero (avoids an undefined ratio for newly started wells). The raw ratio and the applied capped value both appear in the signal description, e.g. `rate ×4.21 (capped ×3.00)`.

**What changed in practice:** two wells with identical cumulative volume, distance, and depth can now score differently if one accelerated injection shortly before the earthquake and the other did not.

---

#### `physics_v1` — pore-pressure diffusion model *(active engine)*
**SWD weight replaces all spatial/temporal decay:**
```
erfc( r_m / 2√(D · t_inject_s) )
```
where `D = 0.5 m²/s` (hydraulic diffusivity), `r_m` = distance in metres, `t_inject_s` = estimated injection duration in seconds.

The complementary error function (`erfc`) is derived from Shapiro et al. (1997) and Biot diffusion theory. It captures the physically correct time–distance interaction that the heuristic model cannot:

| Scenario | Heuristic v4 weight | Physics v1 weight |
|---|---|---|
| Well 5 km away, injecting 6 months | `exp(−5/10) × exp(−180/365)` ≈ 0.33 | `erfc(5000 / 2√(0.5 × 15.8M))` ≈ 0.21 |
| Well 5 km away, injecting 5 years | same 0.33 (time decay different only) | `erfc(5000 / 2√(0.5 × 157.8M))` ≈ 0.73 |
| Well 15 km away, injecting 1 year | `exp(−15/10) × ...` ≈ 0.13 | `erfc(15000 / 2√(0.5 × 31.6M))` ≈ 0.008 |
| Well 15 km away, injecting 5 years | same range | `erfc(15000 / 2√(0.5 × 157.8M))` ≈ 0.23 |

The key insight: **a well 15 km away that has only been injecting for 1 year scores ~0.008** (the pressure front has traveled only ~14 km). The heuristic model would score it at ~0.13 regardless of injection history. After 5 years of injection that same well rises to ~0.23 as the pressure front reaches 31 km.

**Injection duration** is estimated from `first_report_date` (earliest H-10 record within the search window) to the event date. Falls back to `monthly_record_count × 30.44 days` if the date is unavailable. Duration is capped at the window length (10 years by default).

**Signal descriptions** now include the pressure front radius: `"pressure front ≈14.1 km, erfc=0.0076"` — investigators can immediately see whether a well's pressure could have physically reached the earthquake at the time it occurred.

**Frac scoring**, depth mismatch, and rate-change boost are inherited unchanged from `heuristic_v4`.

**Hydraulic diffusivity D** defaults to `0.5 m²/s` (geometric mean of the 0.1–1.0 m²/s range reported by Smye et al. 2024 for Delaware Basin formations). Calibrate with `scripts/calibrate_engine.py --engine physics`.

**What changed in practice:** a well 15 km away with 1 year of injection history now scores ~17× lower than it did under `heuristic_v4`. Wells with long injection histories near the earthquake score meaningfully higher. Time and distance interact — neither alone determines the score.

**Monte Carlo frac uncertainty layer** (added to `physics_v1`):

When `nearby_frac_jobs` is empty, the engine no longer treats `frac_score = 0` as settled fact. FracFocus does not have 100% operator compliance, so an empty result may reflect reporting gaps rather than genuine absence of frac activity. The engine:

1. Reads `EventContextOut.frac_prior_params` — a `FracPriorParams` object assembled by `EventContextService` from a 5× wider FracFocus query (data-driven prior) or Delaware Basin literature defaults (fallback).
2. Runs 1,000 Monte Carlo trials sampling synthetic frac jobs from the prior and scoring them with the same formula as observed jobs.
3. Returns `(mc_frac_score_mean, mc_frac_score_p5, mc_frac_score_p95)` as new fields on `AttributionResult`, plus `adjusted_likely_driver` and `adjusted_confidence` computed using the MC mean in the softmax verdict.

The prior uses:
- **Delaware Basin defaults** (fallback): n_jobs_mean = 2.0, water volume log-normal median ≈ 298k bbl (≈ 12.5M gal), TVD normal mean 7,500 ft ± 1,500 ft
- **Data-driven** (when ≥ 10 FracFocus rows in the 5× radius): log-normal fitted to actual water volumes, normal fitted to actual TVDs, Poisson rate scaled by area ratio

**What changed in practice:** an event with only SWD evidence used to produce `likely_driver = "swd", confidence = 1.0` regardless of how weak the SWD signal was. Now the same event might produce `adjusted_likely_driver = "swd", adjusted_confidence = 0.71` — communicating that 29% of the expected total signal (SWD + possible frac) is attributable to unobserved frac activity.

---

### Factors at a glance

| Factor | h\_v0 | h\_v1 | h\_v2 | h\_v3 | h\_v4 | p\_v1 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Spatial decay SWD `exp(−r/λ)` | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| Temporal decay SWD `exp(−t/λ)` | — | ✓ | ✓ | ✓ | ✓ | — |
| Pore-pressure diffusion `erfc(r/2√Dt)` | — | — | — | — | — | ✓ |
| Depth mismatch penalty (σ=3 km) | — | — | ✓ | ✓ | ✓ | ✓ |
| Log-odds confidence metric | — | — | — | ✓ | ✓ | ✓ |
| Rate-change boost (cap ×3) | — | — | — | — | ✓ | ✓ |
| Spatial decay frac `exp(−r/λ)` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| MC frac uncertainty (when frac absent) | — | — | — | — | — | ✓ |

### Planned improvements (not yet implemented)

- ~~**Injection rate change signal**~~ — implemented in `heuristic_v4`
- ~~**Calibrated λ parameters**~~ — calibration infrastructure implemented (see below); apply after collecting ground truth labels
- ~~**Pore-pressure diffusion model**~~ — implemented as `physics_v1` (see below)
- ~~**Monte Carlo frac uncertainty**~~ — implemented in `physics_v1`; absent frac data is treated as uncertain rather than zero
- **Coulomb stress transfer for frac** — model stress changes on nearby faults using focal mechanism data from the TexNet catalog

### Parameter calibration

All engine parameters (`swd_lambda_km`, `frac_lambda_km`, `time_lambda_days`, `depth_sigma_km`, `rate_boost_cap`) are injectable via `HeuristicAttributionService.__init__`. The module-level constants in `attribution_service.py` remain as production defaults.

The calibration script `scripts/calibrate_engine.py` performs a grid search over these parameters and ranks combinations by binary log-loss against a ground truth CSV:

```bash
# Calibrate heuristic engine (900 combinations: swd_λ, frac_λ, time_λ, depth_σ)
python scripts/calibrate_engine.py data/ground_truth.csv

# Calibrate physics engine (150 combinations: D, frac_λ, depth_σ)
python scripts/calibrate_engine.py data/ground_truth.csv --engine physics

# Also write full results to JSON
python scripts/calibrate_engine.py data/ground_truth.csv --engine physics --top 20 --output results.json
```

**Ground truth CSV format** (`data/ground_truth_template.csv` is a starting point):
```
event_id,driver,notes
tx2025iqwk,swd,Frohlich 2016 Table 2
us7000abcd,frac,RRC enforcement letter 2024-03
```

**What the script does:**
1. Loads ground truth labels (skips `indeterminate` rows — no definite class to score against)
2. Assembles event contexts from the local database **once** — this is the expensive step
3. Sweeps all parameter combinations (currently 6 × 5 × 6 × 5 = 900 combinations, ~seconds)
4. For each combination, computes binary log-loss: `−log(p_correct)` per event, averaged across all labeled events
5. Reports a ranked table and the delta vs. current defaults

**After calibration:** copy the best-fit values into the module constants in `attribution_service.py` and bump `_ENGINE` to the next version label.

**Good sources for ground truth labels in the Delaware Basin:**
- Frohlich et al. (2016) — SWD attribution for Texas earthquakes
- Texas RRC enforcement actions (public record)
- TexNet event reports with operator-confirmed causes
- UT Bureau of Economic Geology case studies

### Swapping in a new engine

The interface is stable. To replace the heuristic engine with a physics-based model:

1. Create a new service class (e.g., `PhysicsAttributionService`) with the method signature `score(context: EventContextOut) -> AttributionResult`
2. Update the `get_attribution_service` factory in `app/api/dependencies.py` to return the new class

No endpoint code, schema definitions, or database migrations are needed.

---

## Running the Platform

**Start the server:**
```bash
python main.py
```
This creates the database (if it doesn't exist), applies schema migrations, and starts the API on port 8000.

**Recommended data loading order:**
```bash
# 1. Seismic catalog
curl -X POST "http://localhost:8000/api/v1/seismic/usgs/fetch?min_magnitude=1.5"
curl -X POST "http://localhost:8000/api/v1/seismic/texnet/fetch?min_magnitude=2.5"

# 2. Station metadata (for coverage context)
curl -X POST http://localhost:8000/api/v1/seismic/iris/stations/fetch

# 3. SWD wells first, then monthly records (H-10 needs the well list)
curl -X POST http://localhost:8000/api/v1/swd/uic/fetch
curl -X POST http://localhost:8000/api/v1/swd/h10/fetch

# 4. FracFocus (or wait for the monthly cron to fire automatically)
curl -X POST http://localhost:8000/api/v1/sync/trigger
```

**Run an analysis:**
```bash
# Get context preview (no snapshot saved)
curl "http://localhost:8000/api/v1/analysis/events/tx2025iqwk/context?swd_radius_km=20"

# Full analysis + save snapshot
curl -X POST "http://localhost:8000/api/v1/analysis/events/tx2025iqwk/analyze"
```

The interactive API docs are available at `http://localhost:8000/docs` once the server is running.

---

## Limitations and Known Caveats

- **SQLite**: The database is a single local file. This is appropriate for a proof-of-concept but would need to be replaced (PostgreSQL with PostGIS) for production-scale concurrent access.
- **No spatial index**: Proximity queries use bounding-box filtering on raw lat/lon columns, then compute exact distances in Python. This is fast enough for the current data volume but would not scale to millions of rows.
- **Attribution model**: The active engine `physics_v1` uses pore-pressure diffusion (`erfc`) for SWD and exponential spatial decay for frac. It assumes a single homogeneous diffusivity value — real formations are heterogeneous, anisotropic, and faulted. Results should be treated as informed estimates, not certified attributions.
- **FracFocus coverage**: FracFocus disclosure is mandatory in Texas but not all operators comply immediately. Some jobs may be missing or delayed. The Monte Carlo frac uncertainty layer addresses this by treating absent frac data as uncertain rather than as zero: when no frac jobs are found, the engine samples plausible frac contributions from a prior fitted to the broader Delaware Basin dataset and reports `adjusted_likely_driver` / `adjusted_confidence` that factor in this uncertainty. However, the MC prior is a statistical estimate — it cannot substitute for actual operator disclosures.
- **TexNet coverage gap**: TexNet was deployed in 2017. Events before that date come from USGS, which has lower magnitude completeness in this region for the pre-TexNet era.
- **No authentication**: The API has no authentication layer. It should not be exposed publicly without adding one.

---

*Document current as of May 2026. Codebase: `main` branch.*
