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

Trigger data ingestion (both are manual-only, no cron for seismic):

```bash
# FracFocus frac disclosure data — monthly cron also fires this automatically
curl -X POST http://localhost:8000/api/v1/sync/trigger

# TexNet seismic catalog — manual only, safe to re-run (idempotent upsert)
curl -X POST "http://localhost:8000/api/v1/seismic/texnet/fetch?min_magnitude=2.5"
```

`pytest` and `httpx` are declared as dev deps but **no test suite exists yet** — there's nothing to run.

## Project Purpose

Delaware Basin PoC: correlate seismic events with nearby saltwater disposal (SWD) injection and hydraulic fracturing activity. Three data buckets — **Seismic**, **SWD**, **Frac** — feed a single SQLite database. Source plans and field mappings live in `documents/Delaware_PoC_Data_Plan_Enhanced.docx`. Per-source implementation notes are in `documents/`.

## Architecture

### Two independent ingestion pipelines

**FracFocus pipeline** (Frac bucket — already complete):
```
POST /api/v1/sync/trigger
  └── SyncService → DownloadService + CsvIngestionService → FracFocusRepository → fracfocus table
```
Also fires automatically on a monthly APScheduler cron (day=1, hour=2 UTC).

**TexNet pipeline** (Seismic bucket — implemented):
```
POST /api/v1/seismic/texnet/fetch
  └── TexNetService → SeismicEventRepository → seismic_events table
```
No cron — always triggered manually. Always does a full re-fetch and upserts (no ETag skip logic).

The two pipelines share the same SQLite file, engine, and `SessionLocal`, but are otherwise independent. Adding more seismic sources (e.g. USGS) means adding another service + endpoint that writes to the same `seismic_events` table.

### Two SQLAlchemy paradigms in the same app

- **`fracfocus` table → SQLAlchemy Core** (`app/repositories/fracfocus_repository.py`). Columns are inferred at runtime from the CSV header on the first sync, so an ORM class would not work. `create_table_if_not_exists` builds the `CREATE TABLE` dynamically, and `ensure_columns` adds new columns via `ALTER TABLE` on subsequent syncs if the upstream schema grows.
- **`sync_state`, `csv_file_state`, `seismic_events` tables → SQLAlchemy ORM** (`app/models/`). Fixed schemas. `Base.metadata.create_all` in `init_db()` creates them. **Both model modules must be imported before `create_all` is called** — `init_db()` does this with bare imports tagged `# noqa: F401`. If you add a new ORM model, add its import there too.

`Base.metadata.create_all` does not add columns to existing tables. If you add a column to an ORM model after the table already exists on disk, the column won't appear until the database file is deleted and recreated, or you handle it with raw `ALTER TABLE` (see how `FracFocusRepository.ensure_columns` does this for the dynamic table).

### Layered dependency injection

All wiring for FastAPI request handlers is in `app/api/dependencies.py` (uses `Depends`). The FracFocus sync has a second wiring point: `app/tasks/scheduler.py::_run_scheduled_sync` manually builds the same service graph because APScheduler runs in its own thread where FastAPI's DI is unavailable. **When adding a dependency to `SyncService`, update both places** or the cron will silently break.

The TexNet / seismic path has no scheduler equivalent — only `app/api/dependencies.py` needs updating for new seismic services.

### FracFocus sync — three layers of skip logic

`SyncService._do_sync` avoids work at three levels, each cheaper than the next:

1. **HEAD request** — compare upstream `ETag` / `Last-Modified` against `sync_state` row. Unchanged → return `skipped`.
2. **ZIP central directory** — `zipfile.ZipFile.infolist()` reads only the end-of-file directory (no decompression). Per-CSV metadata compared against `csv_file_state` rows; only changed CSVs extracted.
3. **Atomic per-CSV replace** — `FracFocusRepository.replace_csv_data` runs `DELETE WHERE source_file = X` + bulk `INSERT` in one transaction. Every row carries `source_file` so partial failures leave the previous data for that file intact.

Preserve the invariant: `sync_state.etag` is only written **after** all CSVs successfully ingest — otherwise a mid-run crash causes future syncs to skip work that never landed.

### TexNet seismic fetch — ArcGIS REST pagination

`TexNetService.fetch_delaware_events()` paginates the ArcGIS REST layer via `resultOffset` / `exceededTransferLimit`. Two-stage trim:

1. **Server-side**: `WHERE EventType = 'earthquake'` (and optional magnitude threshold) sent in the ArcGIS query.
2. **Client-side**: county name checked against the six Delaware Basin counties (`CULBERSON`, `REEVES`, `LOVING`, `WARD`, `WINKLER`, `PECOS`). The bounding box deliberately overshoots into adjacent counties (Presidio, Crane, etc.) — the county check drops them. County names are uppercased before the check because TexNet returns them inconsistently (`Culberson` vs `CULBERSON`).

The upsert in `SeismicEventRepository.upsert_many` is keyed on `event_id`. Re-running fetch refreshes existing rows (TexNet occasionally revises event locations and magnitudes).

### FracFocus sync concurrency lock

`app/services/sync_service.py` holds `_sync_lock` and `_is_running` at **module scope**, not on the instance. Each request creates a new `SyncService` via DI, so instance-level state would not be shared. The lock prevents the cron and a manual trigger from running simultaneously. Don't move this state to `self`.

### Dynamic-column endpoints and SQL-injection guard

`/api/v1/data/distinct/{column}` and `/group/{column}` interpolate `column` directly into raw SQL because the `fracfocus` table schema isn't known at code-write time. `_validate_column` in `app/api/v1/endpoints/data.py` defends against injection by checking the column exists in `PRAGMA table_info`. Any new endpoint taking a user-supplied column name must do the same.

### Settings and the SQLite path side-effect

`app/core/config.py` uses `pydantic-settings` with `@lru_cache` — `Settings()` is constructed once per process. `app/core/database.py` runs at import time and **creates the parent directory** of the SQLite file by stripping the `sqlite:///` prefix from `DATABASE_URL`. If you switch to a non-SQLite backend, that `removeprefix` call will silently no-op.

## Key configuration (`.env` / environment)

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./fracfocus_data/fracfocus.db` | Shared by all pipelines |
| `TEXNET_REST_URL` | `https://maps.texnet.beg.utexas.edu/…/MapServer/0` | ArcGIS layer base URL — append `/query` internally |
| `TEXNET_BBOX_MIN_LAT/MAX_LAT/MIN_LON/MAX_LON` | `28.5 / 32.5 / -105.5 / -102.5` | Delaware Basin bounding box |
| `SYNC_ENABLED` | `true` | Disables APScheduler cron when false |
| `ZIP_URL` | FracFocus download URL | Source ZIP for FracFocus ingestion |
