# Frontend Developer Guide — Delaware Basin Seismic Attribution PoC

## What This App Does (Plain English)

This is a **data exploration and analysis tool** for understanding whether earthquakes in West Texas (the Delaware Basin) were caused by nearby oil-field activity — specifically saltwater disposal (SWD) injection wells or hydraulic fracturing (fracking) operations.

The backend pulls data from five sources, stores everything in a local SQLite database, and exposes a REST API. Your job is to build a UI over that API.

There are **five functional areas** in the UI:

| Area | What It Shows |
|---|---|
| **Dashboard** | System health — what data has been loaded, when |
| **Seismic Events** | Map + list of earthquakes in the region |
| **SWD Wells** | Saltwater disposal injection wells |
| **FracFocus** | Hydraulic fracturing job disclosures |
| **Event Analysis** | The core feature — pick an earthquake, see which nearby wells or frac jobs may have caused it |

---

## Technical Setup

### Base URL
```
http://localhost:8000
```
All endpoints are prefixed with `/api/v1`.

### CORS
The backend does **not** configure CORS headers. During development, either:
- Run your dev server on the same origin, or
- Add a proxy in your dev config (e.g. Vite: `proxy: { '/api': 'http://localhost:8000' }`), or
- Ask the backend to add `CORSMiddleware` for your dev origin.

### Authentication
None. No API keys, no tokens. Every endpoint is open.

### Response format
All responses are JSON. All timestamps are **UTC ISO 8601** strings (e.g. `"2024-03-15T14:22:00"`).

### Health check
```http
GET /health
```
```json
{ "status": "ok" }
```
Use this to check if the backend is running.

---

## Section 1 — Dashboard

The dashboard answers: **"What data do we have and is everything up to date?"**

### 1.1 Sync History Table

Shows the log of every data-loading run across all pipelines.

```http
GET /api/v1/sync/history?limit=50
```

Optional filters:
- `source` — one of `fracfocus`, `uic`, `h10`, `texnet`, `usgs`, `iris`
- `status` — one of `pending`, `running`, `success`, `failed`, `skipped`

**Response:**
```json
{
  "total": 12,
  "limit": 50,
  "items": [
    {
      "id": 1,
      "source": "texnet",
      "status": "success",
      "started_at": "2024-03-15T10:00:00",
      "finished_at": "2024-03-15T10:01:32",
      "rows_inserted": 843,
      "rows_updated": 12,
      "detail": null,
      "created_at": "2024-03-15T10:00:00"
    }
  ]
}
```

**Status badge colors:**

| Status | Color |
|---|---|
| `success` | Green |
| `running` | Blue (animate) |
| `failed` | Red |
| `skipped` | Gray |
| `pending` | Yellow |

**Sources — human-readable labels:**

| `source` value | Display name |
|---|---|
| `fracfocus` | FracFocus (Frac Disclosures) |
| `uic` | SWD Wells (UIC Inventory) |
| `h10` | SWD Monthly Monitor (H-10) |
| `texnet` | TexNet Seismic Catalog |
| `usgs` | USGS Seismic Catalog |
| `iris` | IRIS Seismic Stations |

---

### 1.2 FracFocus Sync Status Card

Shows the state of the monthly FracFocus sync specifically (it has its own checkpoint system).

```http
GET /api/v1/sync/status
```

**Response:**
```json
{
  "zip_url": "https://www.fracfocusdata.org/...",
  "last_sync_at": "2024-03-01T02:00:00",
  "last_sync_status": "success",
  "etag": "abc123",
  "last_modified": "Mon, 01 Mar 2024 00:00:00 GMT",
  "csv_files": [
    {
      "filename": "FracFocusRegistry_01.csv",
      "last_processed_at": "2024-03-01T02:15:00",
      "row_count": 250000
    }
  ]
}
```

Display `last_sync_status` as a badge, `last_sync_at` as a relative time ("3 days ago"), and the CSV files as an expandable table.

---

### 1.3 Trigger Data Loads (Admin Actions)

These buttons kick off data ingestion. They are long-running — show a spinner and poll sync history until status changes from `running` to `success`/`failed`.

| Button | Endpoint | Notes |
|---|---|---|
| Load FracFocus | `POST /api/v1/sync/trigger` | Background task — poll `/sync/history?source=fracfocus` |
| Load TexNet Events | `POST /api/v1/seismic/texnet/fetch` | Optional `?min_magnitude=2.5` |
| Load USGS Events | `POST /api/v1/seismic/usgs/fetch` | Optional `?min_magnitude=1.5` |
| Load IRIS Stations | `POST /api/v1/seismic/iris/stations/fetch` | No params |
| Load SWD Wells | `POST /api/v1/swd/uic/fetch` | Can take minutes — resumable |
| Load SWD Monitor | `POST /api/v1/swd/h10/fetch` | Run after SWD Wells |

**FracFocus trigger response** (unique — runs in background):
```json
{
  "message": "Sync started",
  "triggered_at": "2024-03-15T10:00:00",
  "status": "started"
}
```
If already running: `"status": "already_running"` — show a warning, not an error.

**All other trigger responses** return immediately with a summary:
```json
{
  "status": "success",
  "source": "texnet",
  "fetched": 900,
  "inserted": 843,
  "updated": 12,
  "pages": 0,
  "error": null
}
```

---

## Section 2 — Seismic Events

### 2.1 Event List + Filters

```http
GET /api/v1/seismic/events?page=1&page_size=50
```

Optional filters:
- `source` — `texnet` | `usgs`
- `county` — e.g. `"Reeves"` (case-insensitive, TexNet only)
- `min_magnitude` — e.g. `2.5`

**Response:**
```json
{
  "total": 1423,
  "page": 1,
  "page_size": 50,
  "items": [
    {
      "source": "texnet",
      "event_id": "tx2024abc",
      "magnitude": 3.2,
      "mag_type": "ML",
      "latitude": 31.5,
      "longitude": -103.8,
      "depth": 5.4,
      "event_type": "earthquake",
      "event_date": "2024-03-10T14:22:00",
      "evaluation_status": "final",
      "rms": 0.12,
      "county_name": "Reeves",
      "region_name": "Delaware Basin",
      "station_count": 14,
      "phase_count": 28,
      "place": null,
      "title": null,
      "alternate_ids": null,
      "gap": null
    }
  ]
}
```

**Key fields to display:**

| Field | Display label | Notes |
|---|---|---|
| `event_id` | ID | Clickable — links to Event Analysis |
| `magnitude` | Mag | Color-code: <2=gray, 2-3=yellow, 3-4=orange, 4+=red |
| `mag_type` | Type | Show in small text next to magnitude |
| `event_date` | Date/Time | Format as local time with UTC label |
| `depth` | Depth (km) | How far underground the earthquake happened |
| `latitude` / `longitude` | Location | Use for map pin |
| `county_name` | County | TexNet events only; USGS shows `place` instead |
| `source` | Source | Badge: `TexNet` or `USGS` |
| `evaluation_status` | Status | TexNet: `final`/`preliminary`; USGS: `reviewed`/`automatic` |

**Fields only relevant to specific sources:**

| Field | Source | Meaning |
|---|---|---|
| `county_name`, `region_name`, `station_count`, `phase_count` | TexNet only | Location data |
| `place`, `title`, `alternate_ids`, `gap` | USGS only | Location label, cross-catalog IDs, location uncertainty |

---

### 2.2 Event Map

Plot all events as circles on a map (Leaflet, Mapbox, etc.) centered on the Delaware Basin:

```
Center: 30.5°N, -104.0°W
Bounds: 28.5–32.5°N, -105.5–-102.5°W
```

Circle size = magnitude. Circle color = magnitude band (same as list). Click a circle → open Event Analysis panel.

---

## Section 3 — SWD Wells (Saltwater Disposal)

SWD wells are injection wells that pump wastewater underground. They are one of the two suspected causes of induced earthquakes.

### 3.1 Well List

```http
GET /api/v1/swd/wells?page=1&page_size=50
```

**Response:**
```json
{
  "total": 892,
  "page": 1,
  "page_size": 50,
  "items": [
    {
      "uic_number": "UIC-12345",
      "api_no": "42-389-12345-00",
      "latitude": 31.2,
      "longitude": -103.5,
      "top_inj_zone": 8000.0,
      "bot_inj_zone": 9200.0,
      "max_liq_inj_pressure": 2500.0,
      "activated_flag": true,
      "lease_name": "Smith Ranch Unit",
      "fetched_at": "2024-03-01T00:00:00"
    }
  ]
}
```

**Key fields to display:**

| Field | Display label | Notes |
|---|---|---|
| `uic_number` | UIC # | Unique ID — use as link |
| `api_no` | API # | Standard well identifier in US |
| `latitude` / `longitude` | Location | Map pin |
| `top_inj_zone` / `bot_inj_zone` | Injection zone (ft) | How deep they inject — in **feet** |
| `max_liq_inj_pressure` | Max Pressure (psi) | Regulatory limit |
| `activated_flag` | Active | Boolean — show as badge |
| `lease_name` | Lease | Location name |

---

### 3.2 Monthly Injection Monitor (H-10)

Shows the monthly injection history for a specific well. Call this when a user clicks on a well.

```http
GET /api/v1/swd/monitoring?uic_no=UIC-12345&page=1&page_size=24
```

**Response:**
```json
{
  "total": 48,
  "page": 1,
  "page_size": 24,
  "items": [
    {
      "uic_no": "UIC-12345",
      "report_date": "2024-02-01T00:00:00",
      "inj_press_avg": 1800.0,
      "inj_press_max": 2100.0,
      "vol_liq": 45000.0,
      "vol_gas": 0.0,
      "fetched_at": "2024-03-01T00:00:00"
    }
  ]
}
```

**Recommended visualization:** A bar chart of `vol_liq` (barrels) per month, with a line overlay for `inj_press_avg`. This shows injection volume trends over time — critical context for the attribution analysis.

---

## Section 4 — FracFocus (Hydraulic Fracturing Jobs)

FracFocus is the national fracking disclosure registry. These are one-time events (a well is fracked once, not continuously).

### 4.1 Job List

```http
GET /api/v1/data/?page=1&page_size=50
```

Optional filters:
- `state` — e.g. `"Texas"` (exact match)
- `operator` — e.g. `"Pioneer"` (partial match)

**Response:**
```json
{
  "total": 35000,
  "page": 1,
  "page_size": 50,
  "items": [
    {
      "jobstartdate": "2022-06-15",
      "jobenddate": "2022-06-22",
      "operatorname": "Pioneer Natural Resources",
      "wellname": "Midland Unit 14H",
      "api10": "42-317-45678-00",
      "latitude": 31.85,
      "longitude": -102.3,
      "tvddepth": 11500.0,
      "totalbasenonwatervolume": 0.0,
      "totalbasewatervolume": 18500000.0,
      "statename": "Texas",
      "countyname": "Midland"
    }
  ]
}
```

**Important note about FracFocus column names:** Unlike other endpoints, FracFocus returns raw CSV columns with **no fixed schema**. Column names are lowercased with no spaces (e.g. `jobstartdate`, `operatorname`, `totalbasewatervolume`). Use `GET /api/v1/data/columns` to get the exact list if needed.

**Key columns to display:**

| Column | Display label | Notes |
|---|---|---|
| `api10` | API # | Standard well identifier |
| `operatorname` | Operator | Company that did the frac job |
| `wellname` | Well | Well name |
| `jobstartdate` | Start Date | When fracking began |
| `jobenddate` | End Date | When fracking ended |
| `tvddepth` | Depth (ft) | True vertical depth — how deep |
| `totalbasewatervolume` | Water Used (gal) | Millions of gallons — divide by 42 to get barrels |
| `latitude` / `longitude` | Location | Map pin |
| `countyname` | County | Texas county |

---

### 4.2 Utility Endpoints

Get distinct values for filter dropdowns:

```http
GET /api/v1/data/distinct/statename
```
```json
{ "column": "statename", "count": 4, "values": ["Texas", "New Mexico", ...] }
```

Get count breakdown:
```http
GET /api/v1/data/group/operatorname
```
```json
{ "column": "operatorname", "groups": [{ "value": "Pioneer Natural Resources", "count": 1240 }, ...] }
```

Get total record count:
```http
GET /api/v1/data/stats
```
```json
{ "total_records": 35421 }
```

---

## Section 5 — Event Analysis (The Core Feature)

This is the most important part of the app. A user picks a seismic event and asks: **"What caused this earthquake?"**

The flow is:
1. User selects an event (from the map or list)
2. User optionally adjusts search parameters (radius, time window)
3. User clicks **"Preview Context"** to see what nearby wells/frac jobs exist (no data saved)
4. User clicks **"Run Analysis"** to run the attribution engine and save the result

---

### 5.1 Preview Context (Read-Only)

Fetches all nearby data without running attribution or saving anything.

```http
GET /api/v1/analysis/events/{event_id}/context
```

Optional query params (all have defaults):
- `swd_radius_km` (default: 20) — how far to search for SWD wells
- `swd_window_days` (default: 3650) — how many days back to look for injection activity (10 years)
- `frac_radius_km` (default: 10) — how far to search for frac jobs
- `frac_window_days` (default: 730) — how many days back for frac jobs (2 years)
- `station_radius_km` (default: 50) — how far to search for seismic stations

**Response:**
```json
{
  "event_id": "tx2024abc",
  "event_latitude": 31.5,
  "event_longitude": -103.8,
  "event_depth_km": 5.4,
  "event_date": "2024-03-10T14:22:00",
  "event_magnitude": 3.2,
  "swd_radius_km": 20.0,
  "swd_window_days": 3650,
  "frac_radius_km": 10.0,
  "frac_window_days": 730,
  "station_radius_km": 50.0,
  "nearby_swd_wells": [
    {
      "uic_number": "UIC-12345",
      "api_no": "42-389-12345-00",
      "distance_km": 4.2,
      "latitude": 31.52,
      "longitude": -103.84,
      "top_inj_zone": 8000.0,
      "bot_inj_zone": 9200.0,
      "monthly_record_count": 36,
      "cumulative_bbl": 1250000.0,
      "avg_pressure_psi": 1900.0,
      "max_pressure_psi": 2400.0,
      "first_report_date": "2021-03-01T00:00:00",
      "last_report_date": "2024-02-01T00:00:00",
      "rate_change_ratio": 2.3
    }
  ],
  "nearby_frac_jobs": [
    {
      "api_number": "42-389-67890-00",
      "distance_km": 6.8,
      "latitude": 31.56,
      "longitude": -103.75,
      "job_start_date": "2023-08-01",
      "job_end_date": "2023-08-08",
      "operator_name": "Pioneer Natural Resources",
      "well_name": "Reeves Unit 5H",
      "total_water_volume": 18500000.0,
      "formation_depth": 11500.0
    }
  ],
  "nearby_stations": [
    {
      "network_station": "TX.ELK",
      "network": "TX",
      "station_code": "ELK",
      "distance_km": 22.1,
      "latitude": 31.7,
      "longitude": -103.6,
      "site_name": "Elkhorn Ranch",
      "end_time": null
    }
  ]
}
```

**What to show:**

**Context Map:**
- Center on the earthquake epicenter (star icon)
- Draw radius circles (SWD radius, frac radius, station radius — toggleable)
- SWD wells as orange pins — size = cumulative injection volume
- Frac jobs as purple pins — size = water volume
- Seismic stations as blue triangles

**SWD Wells Table:**

| Column | Display | Notes |
|---|---|---|
| `uic_number` | Well ID | |
| `distance_km` | Distance | Sort ascending by default |
| `cumulative_bbl` | Total Injected (bbl) | Format with commas |
| `avg_pressure_psi` | Avg Pressure (psi) | |
| `top_inj_zone` / `bot_inj_zone` | Injection Zone (ft) | Show as range "8,000 – 9,200 ft" |
| `last_report_date` | Last Active | |
| `rate_change_ratio` | Rate Change | >1 = ramping up (red), <1 = slowing down (green). `null` = unknown |

**`rate_change_ratio` explanation for UI tooltip:** "Compares injection in the 3 months before the earthquake vs. the 9 months before that. A value of 2.3 means injection nearly doubled recently."

**Frac Jobs Table:**

| Column | Display | Notes |
|---|---|---|
| `api_number` | API # | |
| `operator_name` | Operator | |
| `distance_km` | Distance | |
| `job_start_date` | Frac Date | |
| `total_water_volume` | Water Used (gal) | Large number — format with commas |
| `formation_depth` | Depth (ft) | |

**Stations Table:**

| Column | Display | Notes |
|---|---|---|
| `network_station` | Station | e.g. `TX.ELK` |
| `site_name` | Name | |
| `distance_km` | Distance | |
| `end_time` | Status | `null` = Active (green badge), otherwise "Decommissioned" |

---

### 5.2 Run Attribution Analysis (Saves Result)

```http
POST /api/v1/analysis/events/{event_id}/analyze
```

Same optional query params as the context endpoint above.

**Response:**
```json
{
  "snapshot_id": 42,
  "context": { ... },
  "attribution": {
    "engine": "heuristic_v4",
    "likely_driver": "swd",
    "confidence": 0.87,
    "swd_score": 4823.12,
    "frac_score": 712.44,
    "signals": [
      {
        "name": "SWD UIC-12345",
        "value": 3940.22,
        "unit": "weighted_bbl",
        "description": "UIC-12345 — 4.2 km away, 1,250,000 bbl cumulative in window, last report 38d before event, depth Δ1.2 km, rate ×2.30 (capped ×2.30)"
      },
      {
        "name": "FRAC 42-389-67890-00",
        "value": 712.44,
        "unit": "weighted_bbl",
        "description": "Frac job at 6.8 km, started 2023-08-01, 18,500,000 gal (440,476 bbl) water volume, depth Δ3.8 km"
      }
    ]
  }
}
```

---

### 5.3 Attribution Result Display

**Verdict Banner:**

| `likely_driver` | `confidence` | What to show |
|---|---|---|
| `swd` | ≥ 0.75 | "Likely caused by saltwater disposal" (high confidence, red banner) |
| `swd` | 0.5–0.75 | "Possibly caused by saltwater disposal" (medium, orange) |
| `frac` | ≥ 0.75 | "Likely caused by hydraulic fracturing" (high, purple) |
| `frac` | 0.5–0.75 | "Possibly caused by hydraulic fracturing" (medium, orange) |
| `indeterminate` | any | "Cause unclear — insufficient evidence" (gray) |

Show `confidence` as a percentage: `0.87 → 87% confidence`.

**Score Bar:**

```
SWD  ████████████████████░░░░  87%
FRAC ████░░░░░░░░░░░░░░░░░░░░  13%
```

Calculated as `swd_score / (swd_score + frac_score)`.

**Signals List** (ranked — highest value first, already sorted by API):

For each signal, parse the `description` string and display as a card:
- Signal name (`SWD UIC-12345` or `FRAC 42-389-...`)
- Weighted score value + unit
- The description text as-is (it is human-readable)
- Pull out key numbers from description for visual highlights:
  - Distance badge (`4.2 km`)
  - Depth mismatch badge (`Δ1.2 km`) — color: green if <3 km, yellow if 3-6 km, red if >6 km
  - Rate change badge (`×2.30`) — show if present

**`snapshot_id`:** Save this. Each analysis run creates a new permanent snapshot. You can use it for a history feature ("previous analyses for this event").

---

### 5.4 Analysis Parameter Controls

Expose these as sliders or inputs in a collapsible "Advanced Settings" panel:

| Parameter | Label | Default | Min | Max | Unit |
|---|---|---|---|---|---|
| `swd_radius_km` | SWD Search Radius | 20 | 0 | 200 | km |
| `swd_window_days` | SWD Lookback | 3650 | 1 | 36500 | days |
| `frac_radius_km` | Frac Search Radius | 10 | 0 | 200 | km |
| `frac_window_days` | Frac Lookback | 730 | 1 | 36500 | days |
| `station_radius_km` | Station Search Radius | 50 | 0 | 500 | km |

Show human-friendly equivalents: `3650 days ≈ 10 years`, `730 days ≈ 2 years`.

---

## Section 6 — IRIS Seismic Stations

Seismic stations are the listening devices that detect earthquakes. They provide context for how well an earthquake was located. They do not cause earthquakes — they are passive sensors.

```http
GET /api/v1/seismic/iris/stations?page=1&page_size=50
```

Optional filters:
- `network` — e.g. `TX`, `N4`, `IU`
- `active_only=true` — only stations currently operating

**Response:**
```json
{
  "total": 48,
  "page": 1,
  "page_size": 50,
  "items": [
    {
      "network_station": "TX.ELK",
      "network": "TX",
      "station_code": "ELK",
      "latitude": 31.7,
      "longitude": -103.6,
      "elevation": 892.0,
      "site_name": "Elkhorn Ranch",
      "start_time": "2017-01-01T00:00:00",
      "end_time": null
    }
  ]
}
```

Show stations on the map as blue triangles. `end_time: null` = currently active.

---

## Pagination — Universal Pattern

Every list endpoint follows the same pattern:

```http
GET /api/v1/seismic/events?page=1&page_size=50
```

```json
{
  "total": 1423,
  "page": 1,
  "page_size": 50,
  "items": [...]
}
```

- `total` = total records matching filters (not just this page)
- Page is **1-indexed** (first page = `page=1`, not `page=0`)
- Max `page_size` is **1000** for all endpoints
- Total pages = `Math.ceil(total / page_size)`

---

## Error Handling

| HTTP Status | When it happens | What to show |
|---|---|---|
| `404` | Event ID not found (analysis endpoints) | "Event not found. Make sure seismic data has been loaded." |
| `400` | Invalid column name (FracFocus distinct/group) | Show the error message — it includes valid column names |
| `500` | Server error | "Something went wrong on the server. Check if the backend is running." |

All errors return JSON:
```json
{ "detail": "Event tx9999 not found" }
```

---

## Glossary — Terms Frontend Developers Will Encounter

| Term | Plain English meaning |
|---|---|
| **SWD / Saltwater Disposal** | A well that pumps wastewater (from oil production) deep underground |
| **UIC** | The government registration number for an injection well |
| **H-10** | The monthly report form SWD operators file — contains injection volume and pressure data |
| **FracFocus** | National public database where fracking operators disclose what chemicals/water they used |
| **Frac job** | One hydraulic fracturing operation on one well (one-time event) |
| **Magnitude** | How strong an earthquake was. Below 2.5 = rarely felt. Above 4.0 = widely felt. |
| **Depth (km)** | How far underground the earthquake happened. Shallow = more surface damage. |
| **Hypocenter** | The underground point where the earthquake started |
| **Epicenter** | The point on the surface directly above the hypocenter (what you plot on a map) |
| **Attribution** | Which activity (SWD or frac) the algorithm thinks caused the earthquake |
| **Heuristic engine** | The current algorithm — scores wells by distance, volume, time, depth, and rate change |
| **TexNet** | Texas earthquake monitoring network (UT Austin) — detailed local catalog |
| **USGS** | US Geological Survey — national earthquake catalog, historical data going back to 2000 |
| **IRIS / EarthScope** | Organization that operates seismic sensor networks |
| **Delaware Basin** | The geographic region this app covers — West Texas / Southeast New Mexico |
| **bbl** | Barrel — oil-field unit of volume. 1 barrel = 42 US gallons |
| **psi** | Pounds per square inch — unit of pressure |
| **TVD / tvddepth** | True Vertical Depth — how deep a well goes straight down (in feet) |
| **API number** | Standard 10-digit US well identifier. Not related to REST APIs. |
| **RMS** | Root mean square residual — a quality indicator for earthquake location accuracy |
| **Azimuthal gap** | The largest gap in the ring of seismic stations around an earthquake. Larger gap = less precise location. |
| **rate_change_ratio** | Compares recent vs. prior injection rate. >1 means ramping up. <1 means slowing down. |
| `indeterminate` | The attribution algorithm couldn't decide — not enough evidence either way |

---

## Recommended Page/View Structure

```
App
├── /dashboard                  → System health, sync history, trigger buttons
├── /events                     → Seismic event list + map
│   └── /events/:event_id       → Event detail + run analysis
├── /wells                      → SWD well list + map
│   └── /wells/:uic_number      → Well detail + injection history chart
├── /frac                       → FracFocus job list + map
├── /stations                   → IRIS station list + map
└── /admin                      → Data loading controls (can merge into dashboard)
```

The most important navigation flow is:
**Events list → click event → Event Analysis page → run attribution → see verdict**

Everything else is supporting data that gives the analyst context.

---

## Data Load Order (Important for Testing)

The backend pipelines must be loaded in this order before analysis works:

1. Load TexNet or USGS seismic events (to have earthquakes to analyze)
2. Load SWD Wells (`/swd/uic/fetch`) — must complete before step 3
3. Load SWD Monthly Monitor (`/swd/h10/fetch`) — needs well list from step 2
4. Load FracFocus (`/sync/trigger`)
5. Load IRIS Stations (`/iris/stations/fetch`)

Steps 1, 4, and 5 are independent and can run in any order relative to each other.