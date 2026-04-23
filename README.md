# FracFocus Data API

A production-ready FastAPI service that downloads hydraulic fracturing disclosure data from [FracFocus](https://www.fracfocusdata.org), stores it in SQLite, and keeps it up to date with a smart incremental monthly sync.

---

## Features

- **REST API** — query the dataset with pagination and filtering
- **Incremental sync** — checks HTTP `ETag` / `Last-Modified` headers before downloading; only re-processes CSV files that have actually changed inside the ZIP
- **Streaming download** — writes the ZIP directly to disk, never loads 500 MB+ into memory
- **Monthly cron** — APScheduler triggers sync automatically on the 1st of each month at 02:00
- **Manual trigger** — `POST /api/v1/sync/trigger` starts a background sync on demand
- **Atomic CSV replacement** — each CSV file is replaced in a single transaction (DELETE + INSERT), so a mid-run crash leaves the previous data intact
- **Dependency injection** — every service, repository, and DB session is wired through FastAPI `Depends`
- **Structured JSON logging** — all log lines are JSON, friendly to log aggregators

---

## Project Structure

```
fracfocus_data_fetch/
├── app/
│   ├── core/
│   │   ├── config.py               # All settings via pydantic-settings / .env
│   │   ├── database.py             # SQLAlchemy engine, SessionLocal, init_db()
│   │   └── logging.py              # JSON logging setup
│   │
│   ├── models/
│   │   └── sync_state.py           # ORM: SyncState, CsvFileState
│   │
│   ├── schemas/
│   │   ├── sync.py                 # Pydantic: SyncStatusResponse, SyncResult, …
│   │   └── fracfocus.py            # Pydantic: FracFocusListResponse
│   │
│   ├── repositories/
│   │   ├── fracfocus_repository.py      # SQLAlchemy Core: bulk insert, paginated query
│   │   └── sync_state_repository.py     # ORM CRUD for SyncState & CsvFileState
│   │
│   ├── services/
│   │   ├── download_service.py          # HEAD check, streaming download, ZIP metadata
│   │   ├── csv_ingestion_service.py     # Schema inference + ingestion orchestration
│   │   └── sync_service.py              # Full sync flow, thread-safe is_running flag
│   │
│   ├── tasks/
│   │   └── scheduler.py            # APScheduler cron + FastAPI lifespan
│   │
│   └── api/
│       ├── dependencies.py         # FastAPI Depends providers
│       └── v1/
│           ├── router.py
│           └── endpoints/
│               ├── sync.py         # GET /sync/status, POST /sync/trigger
│               └── data.py         # GET /data/, GET /data/stats
│
├── main.py                         # App entry point
├── requirements.txt
└── .env.example
```

---

## Architecture Overview

```
FastAPI endpoint
  └── Depends(get_sync_service)
        └── SyncService
              ├── DownloadService        — HTTP layer (requests)
              ├── CsvIngestionService    — CSV → DB
              │     └── FracFocusRepository   — SQLAlchemy Core (engine)
              ├── SyncStateRepository   — SQLAlchemy ORM (session)
              └── CsvFileStateRepository — SQLAlchemy ORM (session)
```

**Two database technologies side by side:**

| Table | ORM / Core | Why |
|---|---|---|
| `fracfocus` | SQLAlchemy **Core** | Schema is inferred dynamically from the CSV header — no fixed Python class |
| `sync_state` | SQLAlchemy **ORM** | Fixed schema, benefits from session-level change tracking |
| `csv_file_state` | SQLAlchemy **ORM** | Fixed schema, same reason |

---

## Database Schema

### `fracfocus`
Dynamically created on first sync. Columns are inferred from the CSV header (lowercased, spaces → underscores). An extra `source_file` column is added to track which CSV file each row came from.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | auto-increment |
| `source_file` | TEXT | e.g. `FracFocusCSV_1.csv` |
| *(CSV columns)* | TEXT | all remaining columns from the header |

### `sync_state`
One row per source ZIP URL. Tracks the last known `ETag` / `Last-Modified` so we can skip unchanged downloads.

| Column | Type | Notes |
|---|---|---|
| `zip_url` | TEXT UNIQUE | source URL |
| `etag` | TEXT | HTTP ETag from last download |
| `last_modified` | TEXT | HTTP Last-Modified from last download |
| `last_sync_at` | DATETIME | timestamp of last successful sync |
| `last_sync_status` | TEXT | `never` / `running` / `success` / `failed` |
| `error_message` | TEXT | populated on failure |

### `csv_file_state`
One row per CSV file inside the ZIP. Used to detect which files changed without downloading everything.

| Column | Type | Notes |
|---|---|---|
| `filename` | TEXT UNIQUE | basename of the CSV file |
| `file_size` | INTEGER | uncompressed size from ZIP central directory |
| `compress_size` | INTEGER | compressed size from ZIP central directory |
| `last_modified_zip` | TEXT | date_time tuple from ZipInfo |
| `last_processed_at` | DATETIME | when this file was last ingested |
| `row_count` | INTEGER | rows inserted on last run |

---

## Incremental Sync Flow

```
run_sync() called  (cron or POST /trigger)
│
├─ HEAD request → fracfocusdata.org
│   ├─ ETag + Last-Modified unchanged → return { status: "skipped" }
│   └─ changed → continue
│
├─ Stream ZIP to disk  (no full memory load)
│
├─ ZipFile.infolist()  ← reads central directory only, no decompression
│
├─ Compare each ZipInfo against csv_file_state table
│   ├─ file_size changed  → process
│   ├─ compress_size changed → process
│   ├─ last_modified_zip changed → process
│   ├─ no DB record yet → process
│   └─ identical → skip
│
├─ Extract only changed CSV files
│
├─ For each changed CSV:
│   ├─ Infer columns from header
│   ├─ CREATE TABLE IF NOT EXISTS fracfocus (…)
│   └─ Single transaction: DELETE rows WHERE source_file = X, then bulk INSERT
│       (crash-safe: old data survives if insert fails)
│
└─ Update sync_state (new ETag, last_sync_at, status = "success")
    Update csv_file_state (new sizes, last_processed_at, row_count)
```

---

## API Reference

### Health

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Returns `{"status": "ok"}` |

### Sync

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/sync/status` | Last sync time, ETag, per-file row counts |
| POST | `/api/v1/sync/trigger` | Start a sync in the background |

**`GET /api/v1/sync/status` response:**
```json
{
  "zip_url": "https://...",
  "last_sync_at": "2025-01-01T02:00:00",
  "last_sync_status": "success",
  "etag": "\"abc123\"",
  "last_modified": "Mon, 01 Jan 2025 00:00:00 GMT",
  "csv_files": [
    { "filename": "FracFocusCSV_1.csv", "last_processed_at": "...", "row_count": 1250000 }
  ]
}
```

**`POST /api/v1/sync/trigger` response:**
```json
{ "message": "Sync started in background", "triggered_at": "...", "status": "started" }
```
Possible `status` values: `started` | `already_running`

### Data

| Method | Path | Query params | Description |
|---|---|---|---|
| GET | `/api/v1/data/` | `page`, `page_size`, `state`, `operator` | Paginated records |
| GET | `/api/v1/data/stats` | — | Total row count |

**`GET /api/v1/data/` query params:**

| Param | Default | Notes |
|---|---|---|
| `page` | `1` | 1-based |
| `page_size` | `50` | max `1000` |
| `state` | — | exact match on `state_name` column |
| `operator` | — | partial match (LIKE) on `operator_name` column |

---

## Getting Started

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure (optional)

Copy `.env.example` to `.env` and adjust as needed:

```bash
cp .env.example .env
```

All settings have sensible defaults — the app works without a `.env` file.

### 3. Run

```bash
python main.py
# or
uvicorn main:app --host 0.0.0.0 --port 8000
```

The database file is created automatically at `./fracfocus_data/fracfocus.db` on startup.

### 4. Trigger your first sync

```bash
curl -X POST http://localhost:8000/api/v1/sync/trigger
```

Then watch the logs. A full initial sync downloads ~500 MB and inserts several million rows — expect it to take a few minutes.

### 5. Browse the interactive docs

Open `http://localhost:8000/docs` for the Swagger UI.

---

## Configuration Reference

All values can be set via environment variables or a `.env` file.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./fracfocus_data/fracfocus.db` | SQLAlchemy connection string |
| `ZIP_URL` | `https://www.fracfocusdata.org/...` | Source ZIP to download |
| `EXTRACT_DIR` | `./fracfocus_data/extracted` | Where CSV files are extracted |
| `SYNC_ENABLED` | `true` | Enable/disable the APScheduler cron |
| `SYNC_CRON_DAY` | `1` | Day of month to run sync (1 = 1st) |
| `SYNC_CRON_HOUR` | `2` | Hour of day (UTC) to run sync |
| `REQUEST_TIMEOUT` | `120` | HTTP timeout in seconds |
| `DOWNLOAD_CHUNK_SIZE` | `1048576` | Streaming chunk size in bytes (1 MB) |
| `API_HOST` | `0.0.0.0` | Uvicorn bind host |
| `API_PORT` | `8000` | Uvicorn bind port |
| `LOG_LEVEL` | `INFO` | Root log level |

---

## Key Design Decisions

**Why SQLAlchemy Core for the `fracfocus` table?**
The column schema is determined at runtime by reading the CSV header. An ORM model requires columns to be known at import time. Core lets us build and execute SQL dynamically without the ORM overhead, which also makes bulk inserts significantly faster (no per-row Python object creation).

**Why atomic DELETE + INSERT per CSV file?**
If the process crashes mid-insert, the previous data for that file is still intact. A truncate-all-then-insert approach would leave the table empty on failure.

**Why store `source_file` in every row?**
FracFocus ships the dataset as multiple CSV files (`FracFocusCSV_1.csv` … `FracFocusCSV_N.csv`). When only one file changes, we need to replace exactly those rows without touching rows from other files.

**Why read `ZipFile.infolist()` before deciding to extract?**
Python's `zipfile` module reads only the ZIP central directory (located at the end of the file) when opening the archive. This gives us file names, sizes, and dates without decompressing anything — so we can decide which files changed before paying the extraction cost.

**Why a global `threading.Lock` for `is_running`?**
APScheduler runs jobs on background threads; the manual trigger endpoint also runs on a thread pool. Without a lock, two concurrent syncs could corrupt the database. The lock is module-level so it is shared across all `SyncService` instances.
