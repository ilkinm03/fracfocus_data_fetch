# FracFocus Bulk Disclosure Download — Source Documentation

**Data bucket:** Frac  
**Source:** FracFocus (Ground Water Protection Council / Interstate Oil and Gas Compact Commission)  
**Access method:** Bulk ZIP download (CSV files)  
**Download URL:** `https://www.fracfocusdata.org/digitaldownload/FracFocusCSV.zip`

---

## Overview

FracFocus is the national hydraulic fracturing chemical disclosure registry. Operators voluntarily (and in some states, legally) report well-level frac job details: job dates, water volumes, formation depth, and chemical additives. The registry is the primary public source for frac timing and intensity data in the Delaware Basin.

**Why this source is in scope:**
- Provides `JobStartDate` / `JobEndDate` and `TotalWaterVolume` — the two fields that anchor every frac-driver attribution window.
- API numbers (`APINumber`) are the cross-join key into the RRC wellbore database and the RRC UIC injection inventory.
- Delaware Basin coverage starts at ~2011 (when FracFocus launched). RRC Wellbore records fill the pre-2011 gap for completion dates.
- Research directly underpinning the PoC (Aziz Zanjani et al. 2024, *The Seismic Record*) used FracFocus alongside TexNet to correlate HF activity with seismicity in the southern Delaware Basin.

**Delaware trim:** FracFocus is a national dataset. After ingest the `state_number` column (value `42` for Texas) and `county_number` column are used to slice to Delaware Basin counties: Culberson (023), Reeves (137), Loving (169), Ward (185), Winkler (207), Pecos (121). The trim is applied at query time via the API — raw rows for all states are stored.

---

## Architecture

```
APScheduler cron (1st of month, 02:00 UTC)   OR   POST /api/v1/sync/trigger
        │
        ▼
  SyncService.run_sync()                      ← app/services/fracfocus_sync_service.py
        │
        ├── DownloadService.check_remote_changed()   ← HEAD request to fracfocusdata.org
        │       ETag + Last-Modified unchanged → return { status: "skipped" }
        │       changed → continue
        │
        ├── DownloadService.stream_download_to_disk()
        │       Streams ZIP directly to disk (never loads ~500 MB into memory)
        │
        ├── DownloadService.read_zip_csv_infos()
        │       Reads ZIP central directory only — no decompression yet
        │
        ├── CsvFileStateRepository.get_changed_files()
        │       Compares each ZipInfo against csv_file_state table
        │       Only files with changed file_size / compress_size / date_time are processed
        │
        ├── DownloadService.extract_files()
        │       Extracts only the changed CSVs
        │
        └── CsvIngestionService.process_csv()  ← per changed CSV
                │   Infers columns from CSV header
                │   Calls FracFocusRepository.create_table_if_not_exists()
                │   Calls FracFocusRepository.ensure_columns()     ← ALTER TABLE if schema grew
                └── FracFocusRepository.replace_csv_data()
                        Single transaction: DELETE WHERE source_file=X, then bulk INSERT
                        (crash-safe: old data survives if insert fails)

GET /api/v1/sync/status          ← SyncService.get_status()
GET /api/v1/data/                ← FracFocusRepository.get_paginated()
GET /api/v1/data/stats           ← FracFocusRepository.count()
GET /api/v1/data/columns         ← FracFocusRepository.get_table_columns()
GET /api/v1/data/distinct/{col}  ← FracFocusRepository.get_distinct_values()
GET /api/v1/data/group/{col}     ← FracFocusRepository.get_grouped_counts()
```

---

## Configuration

All values have sensible defaults. Override via `.env` or environment variables.

| Variable | Default | Description |
|---|---|---|
| `ZIP_URL` | `https://www.fracfocusdata.org/digitaldownload/FracFocusCSV.zip` | Source ZIP. Change if FracFocus moves the file. |
| `EXTRACT_DIR` | `./fracfocus_data/extracted` | Where CSV files are extracted. ZIP is saved one level up as `fracfocus_latest.zip`. |
| `SYNC_ENABLED` | `true` | Set to `false` to disable the APScheduler cron entirely (manual-only mode). |
| `SYNC_CRON_DAY` | `1` | Day of month to auto-sync (1 = 1st). |
| `SYNC_CRON_HOUR` | `2` | Hour (UTC) to auto-sync. |
| `REQUEST_TIMEOUT` | `120` | HTTP timeout in seconds for both HEAD and streaming GET. |
| `DOWNLOAD_CHUNK_SIZE` | `1048576` | Streaming chunk size in bytes (default 1 MB). |

---

## Data Model

### `fracfocus` table

**Created dynamically** by `FracFocusRepository.create_table_if_not_exists()` on the first sync — there is no SQLAlchemy ORM model for this table. Columns are inferred from the CSV header at ingestion time: lowercased, spaces and hyphens replaced with underscores, parentheses stripped.

Because the schema is determined at runtime, `FracFocusRepository` uses **SQLAlchemy Core** (raw `text()` queries) rather than ORM. If FracFocus adds new columns to a future CSV release, `ensure_columns()` adds them via `ALTER TABLE ADD COLUMN` without touching existing data.

**Always-present columns** (added explicitly regardless of the CSV header):

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `source_file` | TEXT | e.g. `FracFocusCSV_1.csv` — tracks which CSV file the row came from, enabling per-file atomic replacement |

**Key PoC columns** (from the CSV header, exact names depend on FracFocus schema version):

| CSV header → column name | PoC use |
|---|---|
| `APINumber` → `apinumber` | Primary cross-join key into RRC wellbore and UIC records |
| `JobStartDate` → `jobstartdate` | Core field for temporal alignment to a selected seismic event |
| `JobEndDate` → `jobenddate` | Frac window construction, lag calculations |
| `StateNumber` → `statenumber` | Texas filter (value `42`) |
| `CountyNumber` → `countynumber` | Delaware Basin county trim |
| `OperatorName` → `operatorname` | Evidence display and stakeholder review |
| `Latitude` → `latitude` | Map display and spatial join to seismic event |
| `Longitude` → `longitude` | Map display and spatial join |
| `TotalBaseFluidVolume` → `totalbasefluidvolume` | Frac intensity — poroelastic stress proxy |
| `TotalWaterVolume` → `totalwatervolume` | Core frac intensity field |
| `WellName` → `wellname` | User-facing evidence context |
| `FormationDepth` → `formationdepth` | Target depth for injection vs. event depth comparison |

### `sync_state` table

ORM model (`app/models/fracfocus_sync_state.py`). One row per source ZIP URL. Tracks whether the remote file changed between runs.

| Column | Type | Notes |
|---|---|---|
| `zip_url` | TEXT UNIQUE | Source URL |
| `etag` | TEXT | HTTP ETag from last download |
| `last_modified` | TEXT | HTTP Last-Modified from last download |
| `last_sync_at` | DATETIME | Timestamp of last successful sync |
| `last_sync_status` | TEXT | `never` / `running` / `success` / `skipped` / `failed` |
| `error_message` | TEXT | Populated on failure |

### `csv_file_state` table

ORM model (`app/models/fracfocus_sync_state.py`). One row per CSV file inside the ZIP. Enables file-level change detection without re-downloading and re-processing unchanged files.

| Column | Type | Notes |
|---|---|---|
| `filename` | TEXT UNIQUE | Basename of the CSV (e.g. `FracFocusCSV_1.csv`) |
| `file_size` | INTEGER | Uncompressed size from ZIP central directory |
| `compress_size` | INTEGER | Compressed size from ZIP central directory |
| `last_modified_zip` | TEXT | `date_time` tuple from `ZipInfo` (string representation) |
| `last_processed_at` | DATETIME | When this file was last successfully ingested |
| `row_count` | INTEGER | Rows inserted on last run |

---

## API Endpoints

### `GET /api/v1/sync/status`

Returns the last sync timestamp, ETag, and per-CSV row counts. Useful to confirm a sync has completed and see which files were processed.

**Response (`SyncStatusResponse`):**
```json
{
  "zip_url": "https://www.fracfocusdata.org/digitaldownload/FracFocusCSV.zip",
  "last_sync_at": "2025-01-01T02:00:00",
  "last_sync_status": "success",
  "etag": "\"abc123\"",
  "last_modified": "Mon, 01 Jan 2025 00:00:00 GMT",
  "csv_files": [
    {
      "filename": "FracFocusCSV_1.csv",
      "last_processed_at": "2025-01-01T02:14:32",
      "row_count": 1250000
    }
  ]
}
```

`last_sync_status` values: `never` (no sync attempted yet), `running`, `success`, `skipped`, `failed`.

---

### `POST /api/v1/sync/trigger`

Starts a sync in the background. The response is immediate — the actual work runs asynchronously.

**Response (`SyncTriggerResponse`):**
```json
{
  "message": "Sync started in background",
  "triggered_at": "2025-01-15T10:30:00",
  "status": "started"
}
```

`status` values: `started` | `already_running`. If a sync is already in progress (cron or a previous trigger), the response is `already_running` and no second sync is launched.

---

### `GET /api/v1/data/`

Returns paginated FracFocus records from the local database.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int ≥ 1 | `1` | Page number (1-based) |
| `page_size` | int 1–1000 | `50` | Records per page |
| `state` | string | `null` | Exact match on `state_name` column |
| `operator` | string | `null` | Partial match (SQL `LIKE`) on `operator_name` column |

**Response (`FracFocusListResponse`):**
```json
{
  "total": 4200000,
  "page": 1,
  "page_size": 50,
  "items": [
    {
      "id": 1,
      "source_file": "FracFocusCSV_1.csv",
      "apinumber": "42389...",
      "jobstartdate": "2021-03-15",
      "operatorname": "Pioneer Natural Resources",
      ...
    }
  ]
}
```

Each item is a plain dict with all columns present in the `fracfocus` table — the exact set depends on the FracFocus schema version ingested.

---

### `GET /api/v1/data/stats`

Returns the total row count.

```json
{ "total_records": 4200000 }
```

---

### `GET /api/v1/data/columns`

Returns all column names currently in the `fracfocus` table. Useful before calling the `/distinct` and `/group` endpoints to know which column names to use.

```json
{ "columns": ["id", "source_file", "apinumber", "jobstartdate", ...] }
```

---

### `GET /api/v1/data/distinct/{column}`

Returns all distinct non-empty values for the given column, sorted ascending.

**Example:** `GET /api/v1/data/distinct/statename`

```json
{
  "column": "statename",
  "count": 42,
  "values": ["Alabama", "Alaska", "Arizona", ...]
}
```

Returns HTTP 400 if `column` is not in the table — this check also prevents SQL injection.

---

### `GET /api/v1/data/group/{column}`

Returns each distinct value with its row count, sorted by count descending.

**Example:** `GET /api/v1/data/group/operatorname`

```json
{
  "column": "operatorname",
  "groups": [
    { "value": "Pioneer Natural Resources", "count": 42300 },
    { "value": "Diamondback Energy", "count": 38900 }
  ]
}
```

Returns HTTP 400 for unknown columns.

---

## Testing

### 1. Check the sync endpoint before a download

```bash
curl -s http://localhost:8000/api/v1/sync/status | python3 -m json.tool
```

On a fresh database, `last_sync_status` will be `"never"` and `csv_files` will be `[]`.

---

### 2. Verify the remote ZIP is reachable (HEAD check)

```bash
curl -sI https://www.fracfocusdata.org/digitaldownload/FracFocusCSV.zip | grep -E "HTTP|ETag|Last-Modified|Content-Length"
```

Expected: HTTP/1.1 200, an `ETag` header, a `Last-Modified` header, and a `Content-Length` around 500–700 MB. If these headers are absent, the sync will conservatively proceed with a full download regardless.

---

### 3. Start the API and trigger the first sync

A full initial sync downloads ~500 MB and inserts several million rows. Expect 10–30 minutes depending on disk speed.

```bash
# Terminal 1 — start the server and watch logs
python main.py

# Terminal 2 — trigger sync
curl -s -X POST http://localhost:8000/api/v1/sync/trigger | python3 -m json.tool
```

Expected trigger response:
```json
{ "message": "Sync started in background", "triggered_at": "...", "status": "started" }
```

Watch the server logs for progress lines like `Inserted 5,000 rows from FracFocusCSV_1.csv...`.

---

### 4. Poll sync status until complete

```bash
watch -n 10 'curl -s http://localhost:8000/api/v1/sync/status | python3 -m json.tool'
```

When complete, `last_sync_status` becomes `"success"` and each `csv_files` entry shows a `row_count`.

---

### 5. Verify idempotency — re-trigger should skip

After a successful sync, trigger again immediately:

```bash
curl -s -X POST http://localhost:8000/api/v1/sync/trigger | python3 -m json.tool
# Wait a moment, then check status
curl -s http://localhost:8000/api/v1/sync/status | python3 -m json.tool
```

Expected: `last_sync_status` becomes `"skipped"` with no download, because the ETag and Last-Modified headers match what was stored. If FracFocus has published a new release in the meantime, it will proceed with a full sync.

---

### 6. Verify concurrent-sync protection

```bash
# Send two triggers in rapid succession
curl -s -X POST http://localhost:8000/api/v1/sync/trigger &
curl -s -X POST http://localhost:8000/api/v1/sync/trigger
```

One of the two will return `"status": "already_running"`. The global `threading.Lock` in `fracfocus_sync_service.py` prevents two syncs running simultaneously.

---

### 7. Query the stored records

```bash
# Total row count
curl -s http://localhost:8000/api/v1/data/stats | python3 -m json.tool

# First page of records
curl -s "http://localhost:8000/api/v1/data/?page_size=3" | python3 -m json.tool

# Filter by Texas
curl -s "http://localhost:8000/api/v1/data/?state=Texas&page_size=5" | python3 -m json.tool

# Filter by operator (partial match)
curl -s "http://localhost:8000/api/v1/data/?operator=Pioneer&page_size=5" | python3 -m json.tool
```

---

### 8. Explore the dynamic schema

```bash
# See all column names (varies by FracFocus schema version)
curl -s http://localhost:8000/api/v1/data/columns | python3 -m json.tool

# All distinct state names
curl -s http://localhost:8000/api/v1/data/distinct/statename | python3 -m json.tool

# Top operators by job count
curl -s http://localhost:8000/api/v1/data/group/operatorname | python3 -m json.tool
```

---

### 9. Inspect the database directly

```bash
# Per-file row counts (matches what /sync/status reports)
sqlite3 fracfocus_data/fracfocus.db ".mode column" ".headers on" \
  "SELECT source_file, COUNT(*) as rows FROM fracfocus GROUP BY source_file ORDER BY source_file;"

# Confirm sync state
sqlite3 fracfocus_data/fracfocus.db ".mode column" ".headers on" \
  "SELECT zip_url, last_sync_status, last_sync_at, etag FROM sync_state;"

# Confirm per-file state
sqlite3 fracfocus_data/fracfocus.db ".mode column" ".headers on" \
  "SELECT filename, row_count, last_processed_at FROM csv_file_state ORDER BY filename;"
```

---

### 10. Interactive Swagger UI

Open `http://localhost:8000/docs` and expand the **sync** and **data** sections. The `/data/distinct/{column}` and `/data/group/{column}` endpoints require knowing actual column names first — use `/data/columns` in Swagger to get the list before calling them.

---

## Known Behaviors and Edge Cases

**Dynamic schema — column names change across FracFocus versions.** FracFocus has released multiple schema versions (v1–v4). Column names differ between versions (e.g. `APINumber` vs `api_number`). The ingestion service normalises headers to lowercase-underscore format, but the exact column names in the database depend on the version of the ZIP downloaded. Always call `/data/columns` before constructing queries against specific column names.

**`source_file` column is load-bearing.** Every row stores which CSV file it came from (`FracFocusCSV_1.csv`, `FracFocusCSV_2.csv`, etc.). The atomic replace strategy relies on this — `DELETE WHERE source_file = X` before re-inserting means a crash mid-insert leaves the previous data for that file intact. Never drop or update this column manually.

**Skip vs. success status.** A sync that finds no changes returns `"skipped"` but still updates `last_sync_at` to now. `"skipped"` is not an error state — it means FracFocus has not published new data since the last run.

**ZIP-level vs. file-level change detection — two layers.** The sync first checks the HTTP ETag/Last-Modified (ZIP-level). If those changed, it then reads the ZIP central directory to see which individual CSV files changed by comparing `file_size`, `compress_size`, and `last_modified_zip` against the `csv_file_state` table. This means a ZIP that was re-packaged without content changes (different compressed size but same uncompressed content) will still trigger re-ingestion. This is intentional — the conservative approach avoids missing real updates.

**Bulk insert batch size is 5000 rows.** `replace_csv_data` accumulates rows in memory and flushes every 5000. On a multi-million-row CSV this produces many intermediate commits visible in the logs (`Inserted 5,000 rows from FracFocusCSV_1.csv...`). If the process is killed mid-batch, the DELETE has already run but the INSERT is incomplete — the previous data for that file is gone. The database remains consistent (no partial rows), but the file will be re-processed on the next sync since `csv_file_state.last_processed_at` is only updated after `replace_csv_data` returns successfully.

**Column injection protection.** The `/data/distinct/{column}` and `/data/group/{column}` endpoints interpolate the column name into raw SQL. `_validate_column()` checks it against `PRAGMA table_info` before executing — any column name not in the table returns HTTP 400. Do not bypass this check when adding new endpoints that accept user-supplied column names.

**`SYNC_ENABLED=false` disables the cron but not the manual trigger.** Setting `SYNC_ENABLED=false` stops APScheduler from starting, so no automatic monthly sync occurs. The `POST /sync/trigger` endpoint remains fully functional. Use this in development or testing environments to avoid accidental large downloads.
