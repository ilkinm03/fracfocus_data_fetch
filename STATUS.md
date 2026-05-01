# Project Status

This file tracks the current state of the project: what is implemented, what is missing, which endpoints exist, and which tables are in the database.
Update this file whenever something meaningful is added or changed.

---

## Goal

Correlate **seismic events** (earthquakes) in the Delaware Basin with nearby **saltwater disposal (SWD) injection** and **hydraulic fracturing (fracking)** activity.

Three data buckets:
- **Seismic** → earthquake catalogs (TexNet + USGS)
- **Frac** → hydraulic fracturing disclosures (FracFocus)
- **SWD** → saltwater injection records (RRC Texas via Texas Open Data Portal)

---

## Implemented Sources

### Frac Bucket — FracFocus ✅

- **Source:** `https://www.fracfocusdata.org/digitaldownload/FracFocusCSV.zip`
- **Method:** ~500 MB ZIP streamed to disk → CSV parsing → SQLite
- **Table:** `fracfocus` (dynamic schema — columns inferred from CSV header at ingest time)
- **Supporting tables:** `sync_state`, `csv_file_state`
- **Cron:** 1st of every month at 02:00 UTC (APScheduler)
- **Skip logic:** 3 layers — ETag/HEAD check → ZIP central directory metadata → atomic per-CSV replace
- **Key columns:** `apinumber`, `jobstartdate`, `jobenddate`, `totalwatervolume`, `latitude`, `longitude`, `statenumber`, `countynumber`, `operatorname`
- **Files:**
  - `app/services/fracfocus_download_service.py`
  - `app/services/fracfocus_ingestion_service.py`
  - `app/services/fracfocus_sync_service.py`
  - `app/repositories/fracfocus_repository.py`
  - `app/repositories/fracfocus_sync_state_repository.py`
  - `app/models/fracfocus_sync_state.py`
  - `app/api/v1/endpoints/fracfocus.py`
  - `app/api/v1/endpoints/fracfocus_sync.py`
  - `app/tasks/fracfocus_scheduler.py`

---

### Seismic Bucket — TexNet ✅

- **Source:** TexNet ArcGIS REST (Bureau of Economic Geology, UT Austin)
- **URL:** `https://maps.texnet.beg.utexas.edu/arcgis/rest/services/catalog/catalog_all/MapServer/0`
- **Method:** ArcGIS `resultOffset` pagination → upsert on `event_id`
- **Table:** `seismic_events` (`source = "texnet"`)
- **County filter (client-side):** CULBERSON, REEVES, LOVING, WARD, WINKLER, PECOS
- **Bounding box:** lat 28.5–32.5°N, lon -105.5–-102.5°W
- **Key columns:** `event_id`, `magnitude`, `event_date`, `latitude`, `longitude`, `depth`, `county_name`, `phase_count`, `station_count`, `evaluation_status`
- **Files:**
  - `app/services/texnet_service.py`
  - `app/repositories/seismic_repository.py`
  - `app/models/seismic_event.py`
  - `app/api/v1/endpoints/seismic.py`

---

### Seismic Bucket — USGS ✅

- **Source:** USGS FDSN Event API (National Earthquake Information Center)
- **URL:** `https://earthquake.usgs.gov/fdsnws/event/1/query`
- **Method:** GeoJSON pagination (1-based offset) → upsert on `event_id`
- **Table:** `seismic_events` (`source = "usgs"`)
- **Coverage:** from `2000-01-01` onward (`USGS_START_TIME` env var — critical, see caveats)
- **No county:** USGS does not return county names; bounding box is the only spatial filter
- **Cross-catalog linkage:** `alternate_ids` column stores all network IDs for the same physical event (e.g. `",us7000s8ml,tx2025iqwk,"`) — used to reconcile USGS and TexNet records
- **Key columns:** `event_id`, `magnitude`, `event_date`, `latitude`, `longitude`, `depth`, `place`, `title`, `alternate_ids`, `gap`, `evaluation_status`
- **Files:**
  - `app/services/usgs_service.py`
  - (shares `seismic_repository.py`, `seismic_event.py`, `seismic.py` with TexNet)

---

### SWD Bucket — RRC Texas (UIC + H-10) ✅

- **Source:** Texas Open Data Portal (Socrata) — Railroad Commission of Texas
- **UIC well inventory URL:** `https://data.texas.gov/resource/xqbh-ev3f.json`
- **H-10 monitoring URL:** `https://data.texas.gov/resource/rqd2-5k7j.json`
- **Bounding box filter:** lat 28.5–32.5°N, lon -105.5–-102.5°W (Delaware Basin)

#### Two-step ingestion

| Step | Endpoint | What it fetches | Table |
|------|----------|-----------------|-------|
| 1 — UIC | `POST /swd/uic/fetch` | Static well metadata (location, type, pressure limits) | `swd_wells` |
| 2 — H-10 | `POST /swd/h10/fetch` | Monthly injection records (pressure, volume) for all UIC wells in DB | `swd_monthly_monitor` |

#### UIC → H-10 join key

`swd_wells.uic_number` ↔ `swd_monthly_monitor.uic_no`

#### Resumable checkpoints

Both fetches are interruptible and resume automatically from exactly where they stopped:

- **UIC:** resumes from last Socrata page offset (`swd_fetch_checkpoint`, `source = "uic"`)
- **H-10:** resumes from last 500-well chunk AND last Socrata page within that chunk (`progress_value` = chunk index, `secondary_value` = page offset)

#### Files

- `app/services/uic_service.py`
- `app/services/h10_service.py`
- `app/repositories/swd_repository.py`
- `app/models/swd.py`
- `app/api/v1/endpoints/swd.py`
- `app/schemas/swd.py`

---

## Missing Sources

### PDQ Production ❌ (deferred)

- **URL:** `https://mft.rrc.texas.gov/link/1f5ddb8d-329a-4459-b7f8-177b4f5ee60d` — returns 404
- Not used in the attribution engine → deferred indefinitely

---

## Database Tables

```
fracfocus_data/fracfocus.db
├── fracfocus              ← Frac data (dynamic columns, inferred from CSV header)
├── seismic_events         ← Unified seismic catalog (texnet + usgs)
├── iris_stations          ← EarthScope seismic station metadata
├── sync_state             ← FracFocus ZIP ETag / Last-Modified tracking
├── csv_file_state         ← Per-CSV file change detection
├── swd_wells              ← UIC injection well inventory (Delaware Basin)
├── swd_monthly_monitor    ← H-10 monthly injection readings
├── swd_fetch_checkpoint   ← Resume state for UIC and H-10 fetches
└── sync_history           ← Full run history across all pipelines
```

---

## API Endpoints

### Sync (`/api/v1/sync/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/status` | Last sync status, ETag, per-CSV row counts |
| POST | `/trigger` | Start a FracFocus sync in the background |
| GET | `/history` | Full run history across all pipelines (filter: `source`, `status`) |

### FracFocus Data (`/api/v1/data/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Paginated records (filters: `state`, `operator`) |
| GET | `/stats` | Total row count |
| GET | `/columns` | All column names currently in the fracfocus table |
| GET | `/distinct/{column}` | All distinct non-empty values for a column |
| GET | `/group/{column}` | Distinct value + row count pairs |

### Seismic (`/api/v1/seismic/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/texnet/fetch` | Fetch TexNet events and upsert into database |
| POST | `/usgs/fetch` | Fetch USGS events and upsert into database |
| GET | `/events` | Query seismic catalog (filters: `source`, `county`, `min_magnitude`) |
| POST | `/iris/stations/fetch` | Fetch EarthScope station metadata |
| GET | `/iris/stations` | List stations |

### SWD (`/api/v1/swd/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/uic/fetch` | Fetch UIC well inventory (resumable) |
| POST | `/h10/fetch` | Fetch H-10 monthly monitoring (resumable) |
| GET | `/wells` | List UIC wells (paginated) |
| GET | `/monitoring` | List H-10 monthly records (paginated, filter: `uic_no`) |

---

## Configuration (`.env`)

| Variable | Default | Notes |
|----------|---------|-------|
| `DATABASE_URL` | `sqlite:///./fracfocus_data/fracfocus.db` | Shared by all pipelines |
| `SYNC_ENABLED` | `true` | `false` disables cron |
| `REQUEST_TIMEOUT` | `120` | HTTP timeout in seconds |
| `TEXNET_BBOX_*` | 28.5 / 32.5 / -105.5 / -102.5 | Delaware Basin bounding box |
| `USGS_START_TIME` | `2000-01-01` | **Critical** — without this USGS returns only last 30 days |
| `SOCRATA_APP_TOKEN` | *(empty)* | Raises Socrata rate limit from 1 to 10 req/s |

---

## Known Caveats

- **No test suite:** `pytest` and `httpx` are declared as dev deps but no test files exist yet
- **FracFocus schema is dynamic:** Always call `GET /api/v1/data/columns` before querying specific columns
- **`evaluation_status` namespaces differ:** TexNet uses `"final"` / `"preliminary"`; USGS uses `"reviewed"` / `"automatic"`
- **H-10 fetch can take hours:** ~1.4M+ records; a `SOCRATA_APP_TOKEN` is strongly recommended
- **Socrata DNS reliability:** Configure a public resolver (e.g. 8.8.8.8) if requests fail — both services include 5-retry exponential backoff

---

## Next Steps

1. **Correlation / attribution engine** — join frac + seismic + SWD within spatial/temporal windows
2. **Test suite** (pytest + httpx)
3. **Frontend** (map-based visualization, optional)
