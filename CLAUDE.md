# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Run the API (creates `./fracfocus_data/fracfocus.db` and starts the APScheduler cron on startup):

```bash
python main.py
# or
uvicorn main:app --host 0.0.0.0 --port 8000
```

Install deps (project uses `uv` — `uv.lock` is committed):

```bash
uv sync                  # preferred
pip install -r requirements.txt   # fallback
```

Trigger data ingestion (all seismic fetches are manual-only):

```bash
# FracFocus — monthly cron also fires this automatically
curl -X POST http://localhost:8000/api/v1/sync/trigger

# TexNet seismic catalog (Delaware Basin, ArcGIS REST)
curl -X POST "http://localhost:8000/api/v1/seismic/texnet/fetch?min_magnitude=2.5"

# USGS FDSN seismic catalog (historical coverage from USGS_START_TIME)
curl -X POST "http://localhost:8000/api/v1/seismic/usgs/fetch?min_magnitude=1.5"
```

`pytest` and `httpx` are declared as dev deps but **no test suite exists yet** — there's nothing to run.

## Project Purpose

Delaware Basin PoC: correlate seismic events with nearby saltwater disposal (SWD) injection and hydraulic fracturing activity. Three data buckets — **Seismic**, **SWD**, **Frac** — feed a single SQLite database. Source plans and field mappings live in `documents/Delaware_PoC_Data_Plan_Enhanced.docx`. Per-source implementation notes are in `documents/`.

**Implemented sources:**
- Frac: FracFocus bulk CSV download → `fracfocus` table
- Seismic: TexNet ArcGIS REST → `seismic_events` table (tagged `source="texnet"`)
- Seismic: USGS FDSN GeoJSON → `seismic_events` table (tagged `source="usgs"`)

## Architecture

### Three independent ingestion pipelines, one database

**FracFocus pipeline** (Frac bucket):
```
POST /api/v1/sync/trigger   (+ monthly APScheduler cron, day=1, hour=2 UTC)
  └── SyncService → DownloadService + CsvIngestionService → FracFocusRepository → fracfocus table
```

**TexNet pipeline** (Seismic bucket):
```
POST /api/v1/seismic/texnet/fetch
  └── TexNetService → SeismicEventRepository → seismic_events table  (source="texnet")
```

**USGS pipeline** (Seismic bucket):
```
POST /api/v1/seismic/usgs/fetch
  └── USGSService → SeismicEventRepository → seismic_events table  (source="usgs")
```

All three share the same SQLite file, engine, and `SessionLocal`. The two seismic pipelines write to the same `seismic_events` table, distinguished by the `source` column. Only FracFocus has a cron; seismic fetches are always manual.

### Multi-catalog `seismic_events` table

Both TexNet and USGS write to `seismic_events` (`app/models/seismic_event.py`). Columns split into three groups:

- **Common** — `source`, `event_id`, `magnitude`, `mag_type`, `latitude`, `longitude`, `depth`, `event_type`, `event_date`, `evaluation_status`, `rms`
- **TexNet-specific** — `phase_count`, `region_name`, `county_name`, `station_count`
- **USGS-specific** — `place`, `title`, `alternate_ids` (comma-separated cross-catalog IDs), `gap`

TexNet events populate `county_name`; USGS events leave it null (no county in the API response). Cross-catalog reconciliation uses `alternate_ids`, which USGS populates with every known network ID for the same physical event (e.g. `",us7000s8ml,tx2025iqwk,"`).

`evaluation_status` uses different value sets: TexNet uses `"final"` / `"preliminary"`, USGS uses `"reviewed"` / `"automatic"`. Treat them as separate namespaces when filtering.

### ORM schema migrations — `_ensure_seismic_columns()`

`Base.metadata.create_all` creates tables but never alters existing ones. `app/core/database.py` contains `_ensure_seismic_columns()`, called from `init_db()`, which runs `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` for any column present in the ORM model but absent in the live table. This is how new columns (`source`, `place`, `title`, `alternate_ids`, `gap`) were added to the already-populated `seismic_events` table without data loss. Apply the same pattern when adding columns to any ORM table in the future.

The `fracfocus` table uses a different mechanism: `FracFocusRepository.ensure_columns()` handles it because the schema is inferred dynamically from the CSV header.

### Two SQLAlchemy paradigms in the same app

- **`fracfocus` table → SQLAlchemy Core** (`app/repositories/fracfocus_repository.py`). Schema inferred at runtime from the CSV header — no fixed ORM class possible. `create_table_if_not_exists` + `ensure_columns` manage the lifecycle.
- **`sync_state`, `csv_file_state`, `seismic_events` → SQLAlchemy ORM** (`app/models/`). Fixed schemas. `Base.metadata.create_all` creates them; `_ensure_seismic_columns` patches the seismic table. **Both model modules must be imported before `create_all` is called** — `init_db()` does this with bare imports tagged `# noqa: F401`. Add any new ORM model there too.

### Layered dependency injection

All FastAPI wiring lives in `app/api/dependencies.py`. The FracFocus sync has a **second wiring point**: `app/tasks/fracfocus_scheduler.py::_run_scheduled_sync` manually reconstructs the same service graph because APScheduler runs in its own thread where FastAPI `Depends` is unavailable. **When adding a dependency to `SyncService`, update both files** or the cron will silently use stale wiring.

The seismic pipelines (TexNet, USGS) have no scheduler equivalent — `app/api/dependencies.py` is the only place to update.

### USGS `starttime` — critical default behaviour

The USGS FDSN API defaults to the **last 30 days** when no `starttime` is provided. At M≥3.5 this returns only a handful of events. `USGS_START_TIME` (default `2000-01-01`) is sent on every request to get full historical coverage. Without it, historical pre-TexNet data (the primary reason for including USGS) is silently absent.

### FracFocus sync — three layers of skip logic

`SyncService._do_sync` avoids work at three levels:

1. **HEAD request** — compare upstream `ETag` / `Last-Modified` against `sync_state` row. Unchanged → return `skipped`.
2. **ZIP central directory** — `zipfile.ZipFile.infolist()` reads metadata only (no decompression). Per-CSV metadata compared against `csv_file_state` rows; only changed CSVs are extracted.
3. **Atomic per-CSV replace** — `FracFocusRepository.replace_csv_data` runs `DELETE WHERE source_file = X` + bulk `INSERT` in one transaction. Every row carries `source_file` so partial failures leave the previous data intact.

Preserve the invariant: `sync_state.etag` is written **only after** all CSVs successfully ingest.

### FracFocus sync concurrency lock

`app/services/fracfocus_sync_service.py` holds `_sync_lock` and `_is_running` at **module scope**, not on the instance. Each request creates a new `SyncService` via DI — instance-level state would not be shared across requests. Don't move this to `self`.

### Dynamic-column endpoints and SQL-injection guard

`/api/v1/data/distinct/{column}` and `/group/{column}` interpolate `column` directly into raw SQL. `_validate_column` in `app/api/v1/endpoints/fracfocus.py` defends against injection by checking the column exists in `PRAGMA table_info`. Any new endpoint taking a user-supplied column name must do the same.

### Settings and the SQLite path side-effect

`app/core/config.py` uses `pydantic-settings` with `@lru_cache` — `Settings()` is constructed once per process. `app/core/database.py` runs at import time and **creates the parent directory** of the SQLite file by stripping the `sqlite:///` prefix from `DATABASE_URL`. Switching to a non-SQLite backend requires fixing that `removeprefix` call.

## Key configuration (`.env` / environment)

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./fracfocus_data/fracfocus.db` | Shared by all pipelines |
| `ZIP_URL` | FracFocus download URL | Source ZIP for FracFocus ingestion |
| `SYNC_ENABLED` | `true` | Disables APScheduler cron when false |
| `TEXNET_REST_URL` | `https://maps.texnet.beg.utexas.edu/…/MapServer/0` | ArcGIS layer base URL — `/query` appended internally |
| `TEXNET_BBOX_MIN_LAT/MAX_LAT/MIN_LON/MAX_LON` | `28.5 / 32.5 / -105.5 / -102.5` | Delaware Basin bbox — shared by TexNet and USGS |
| `USGS_FDSN_URL` | `https://earthquake.usgs.gov/fdsnws/event/1/query` | FDSN Event API endpoint |
| `USGS_MIN_MAGNITUDE` | `1.5` | Default floor when not passed as a query param |
| `USGS_START_TIME` | `2000-01-01` | **Must be set** — without it USGS returns only the last 30 days |
