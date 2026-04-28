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

Manually trigger a sync (otherwise it runs monthly via cron, day=1 hour=2 UTC):

```bash
curl -X POST http://localhost:8000/api/v1/sync/trigger
```

`pytest` and `httpx` are declared as dev deps but **no test suite exists yet** — there's nothing to run.

## Architecture

This is a FastAPI service that mirrors the FracFocus hydraulic-fracturing disclosure ZIP (~500 MB) into a local SQLite database, exposing it via a paginated REST API. The non-obvious bits:

### Two SQLAlchemy paradigms in the same app

- **`fracfocus` table → SQLAlchemy Core** (`app/repositories/fracfocus_repository.py`). Columns are inferred at runtime from the CSV header on the first sync, so an ORM class would not work. `create_table_if_not_exists` builds the `CREATE TABLE` dynamically, and `ensure_columns` adds new columns on subsequent syncs if the upstream schema grows.
- **`sync_state` and `csv_file_state` tables → SQLAlchemy ORM** (`app/models/sync_state.py`). Fixed schemas, accessed through a `Session` (`app/repositories/sync_state_repository.py`).

`Base.metadata.create_all` only creates the ORM tables; the `fracfocus` table is created lazily during the first ingestion. Both sit on the same `engine`.

### Layered dependency injection

The call graph (top to bottom): endpoint → `SyncService` → (`DownloadService` + `CsvIngestionService` + repos). Wiring lives in two places that **must stay in sync**:

- `app/api/dependencies.py` — for FastAPI request handlers (uses `Depends`).
- `app/tasks/scheduler.py::_run_scheduled_sync` — manually rebuilds the same graph because APScheduler runs in its own thread where FastAPI's DI is unavailable.

When adding a new dependency to `SyncService`, **update both** call sites or the cron will break at runtime.

### Sync concurrency and the module-level lock

`app/services/sync_service.py` holds `_sync_lock` and `_is_running` at **module scope**, not on the instance. This is intentional: each request creates a new `SyncService` via DI, but they must all see the same "is a sync running?" flag. The lock guards against the cron and a manual `POST /sync/trigger` racing each other. Don't move this state onto `self`.

### Incremental sync — three layers of skip logic

The sync flow (`SyncService._do_sync`) avoids work at three levels, each cheaper than the next:

1. **HEAD request** — compare upstream `ETag` / `Last-Modified` against `sync_state` row. If unchanged, return `skipped` without downloading.
2. **ZIP central directory** — `zipfile.ZipFile.infolist()` reads only the directory at the end of the file (no decompression). Per-CSV `file_size` / `compress_size` / `date_time` are compared against `csv_file_state` rows. Only changed CSVs are extracted.
3. **Atomic per-CSV replace** — `FracFocusRepository.replace_csv_data` runs `DELETE WHERE source_file = X` + bulk `INSERT` in **one transaction**. This is why every row carries a `source_file` column; partial failures leave the previous data for that file intact.

If you change the sync flow, preserve the invariant that `sync_state.etag` is only updated **after** all CSVs were successfully ingested — otherwise a mid-run crash would cause future syncs to skip work that never landed.

### Dynamic-column endpoints and SQL-injection guard

`/api/v1/data/distinct/{column}` and `/group/{column}` interpolate `column` directly into raw SQL because the table schema isn't known at code-write time. `_validate_column` in `app/api/v1/endpoints/data.py` defends against injection by requiring the column to appear in `PRAGMA table_info`. Any new endpoint that takes a column name as a path/query parameter must call `_validate_column` (or equivalent) before building SQL.

### Settings and the SQLite path side-effect

`app/core/config.py` uses `pydantic-settings` with `@lru_cache`, so `Settings()` is constructed once. `app/core/database.py` runs at import time and **creates the parent directory** of the SQLite file from `DATABASE_URL` (stripping the `sqlite:///` prefix). If you switch to a non-SQLite backend, that `removeprefix` logic will silently no-op — handle the URL parsing properly.