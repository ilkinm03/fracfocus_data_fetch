# USGS FDSN Event API — Source Documentation

**Data bucket:** Seismic  
**Source:** U.S. Geological Survey (USGS) — National Earthquake Information Center  
**Access method:** FDSN Event Web Service (GeoJSON)  
**Query endpoint:** `https://earthquake.usgs.gov/fdsnws/event/1/query`

---

## Overview

The USGS FDSN Event API is the U.S. national earthquake catalog, maintained by the National Earthquake Information Center (NEIC). It aggregates data from seismic networks across the country, including the Texas network (`tx`) that operated in the Delaware Basin before TexNet was established in 2017.

**Why this source is in scope:**
- Provides historical coverage from 2000 onward — TexNet only began operations in 2017, leaving a 17-year gap for pre-induced-seismicity baseline context.
- Returns events from the Texas network (`tx` prefix IDs) that directly overlap with TexNet records, making it the primary cross-catalog reconciliation tool.
- The `properties.ids` field (stored as `alternate_ids`) lists every network ID assigned to the same event, enabling direct joins between USGS and TexNet records for the same physical earthquake.
- Includes events recorded by national (`us`) and regional networks that TexNet may not catalog, broadening spatial coverage near the basin edges.

**Relationship to TexNet:** The two catalogs overlap significantly for post-2017 events. Many events appear in both, with different primary IDs (`tx2021xyz` in USGS vs. `texnet2021xyz` in TexNet). The `alternate_ids` column stores the raw `properties.ids` string (e.g. `",us7000pwzs,tx2025iqwk,"`), which is the primary linkage key for cross-catalog reconciliation.

**No county-level trim:** Unlike TexNet, USGS does not return county names. Events are spatially bounded by the Delaware Basin bounding box. The `place` field (e.g. `"22 km WNW of Mentone, Texas"`) provides human-readable location context.

---

## Architecture

```
POST /api/v1/seismic/usgs/fetch
        │
        ▼
  USGSService.fetch_delaware_events()         ← app/services/usgs_service.py
        │   Sends starttime + bbox + minmagnitude + eventtype=earthquake
        │   Paginates via 1-based offset until len(features) < page_size
        │   Guards client-side: drops any non-earthquake features
        ▼
  SeismicEventRepository.upsert_many()        ← app/repositories/seismic_repository.py
        │   Upserts keyed on event_id (idempotent — safe to re-run)
        │   Updates existing rows — picks up USGS magnitude revisions
        ▼
  seismic_events  (SQLite table)              ← app/models/seismic_event.py
        │   Shared with TexNet via source column
        ▼
GET /api/v1/seismic/events?source=usgs
        │
        ▼
  SeismicEventRepository.get_paginated()
        │   Supports source + min_magnitude filters
        ▼
  JSON response  →  SeismicEventListResponse
```

---

## Configuration

All values have sensible defaults. Override via `.env` or environment variables.

| Variable | Default | Description |
|---|---|---|
| `USGS_FDSN_URL` | `https://earthquake.usgs.gov/fdsnws/event/1/query` | FDSN Event API query endpoint |
| `USGS_MIN_MAGNITUDE` | `1.5` | Default magnitude floor when `min_magnitude` is not passed to the endpoint. Matches the threshold used in the Delaware data plan example query. |
| `USGS_START_TIME` | `2000-01-01` | Start of the historical window. **Without this, the USGS API defaults to the last 30 days only** — historical coverage would be lost. ISO 8601 date string. |
| `REQUEST_TIMEOUT` | `120` | HTTP timeout in seconds (shared with all sources) |
| `TEXNET_BBOX_MIN_LAT/MAX_LAT/MIN_LON/MAX_LON` | `28.5 / 32.5 / -105.5 / -102.5` | Delaware Basin bounding box — shared with TexNet |

**Important:** `USGS_START_TIME` is the most critical USGS-specific setting. Changing it to `1990-01-01` captures more pre-basin-development baseline data; moving it forward to `2017-01-01` restricts coverage to the TexNet overlap period for reconciliation-only use.

---

## Data Model

Table: `seismic_events` (SQLAlchemy ORM, `app/models/seismic_event.py`) — shared with TexNet.

### Columns populated by USGS

| Column | Type | Source field | PoC use |
|---|---|---|---|
| `source` | TEXT | *(set to `"usgs"`)* | Catalog attribution — allows filtering USGS vs. TexNet events |
| `event_id` | TEXT (unique) | `feature.id` | Primary event key for evidence lineage (e.g. `tx2025iqwk`) |
| `magnitude` | FLOAT | `properties.mag` | Event ranking, filtering, attribution context |
| `mag_type` | TEXT | `properties.magType` | Magnitude method (`ml`, `mw`, etc.) |
| `latitude` | FLOAT | `geometry.coordinates[1]` | Map display, spatial join to SWD / frac wells |
| `longitude` | FLOAT | `geometry.coordinates[0]` | Map display, spatial join |
| `depth` | FLOAT | `geometry.coordinates[2]` | Injection depth vs. event depth comparison (km) |
| `event_type` | TEXT | `properties.type` | Always `"earthquake"` after server + client filter |
| `event_date` | DATETIME (UTC) | `properties.time` | Core timestamp for attribution time windows and temporal joins |
| `evaluation_status` | TEXT | `properties.status` | `"reviewed"` / `"automatic"` — prefer reviewed events |
| `rms` | FLOAT | `properties.rms` | Waveform fit quality — confidence signal |
| `place` | TEXT | `properties.place` | Human-readable location label (e.g. `"22 km WNW of Mentone, Texas"`) |
| `title` | TEXT | `properties.title` | Display-ready event label (e.g. `"M 3.7 - 22 km WNW of Mentone, Texas"`) |
| `alternate_ids` | TEXT | `properties.ids` | Comma-separated all-network IDs for this event (e.g. `",us7000s8ml,tx2025iqwk,"`) — primary cross-catalog join key |
| `gap` | FLOAT | `properties.gap` | Azimuthal gap in degrees — location uncertainty proxy |
| `fetched_at` | DATETIME | *(internal)* | Timestamp of last successful upsert |

### Columns left null by USGS

USGS does not provide county-level attribution. The following TexNet-specific columns are always `null` for USGS rows:

| Column | Reason absent |
|---|---|
| `phase_count` | TexNet-specific network metric |
| `region_name` | TexNet-specific region label |
| `county_name` | Not returned by USGS FDSN |
| `station_count` | TexNet-specific network metric |

Use `place` and the bounding box as the Delaware Basin spatial filter for USGS events.

### Non-stored fields

Available in the raw GeoJSON but excluded per the data plan: `properties.updated`, `properties.tz`, `properties.url`, `properties.detail`, `properties.felt`, `properties.cdi`, `properties.mmi`, `properties.alert`, `properties.tsunami`, `properties.sig`, `properties.net`, `properties.code`, `properties.sources`, `properties.types`, `properties.nst`, `properties.dmin`.

---

## API Endpoints

### `POST /api/v1/seismic/usgs/fetch`

Fetches events from USGS FDSN and upserts them into the `seismic_events` table. Safe to call repeatedly — events are matched on `event_id`, so re-fetching refreshes existing rows with any USGS revisions.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `min_magnitude` | float | `USGS_MIN_MAGNITUDE` (1.5) | Magnitude floor. Applied server-side via the API's `minmagnitude` parameter. |

**Response (`SeismicFetchResult`):**
```json
{
  "status": "success",
  "source": "usgs",
  "fetched": 221,
  "inserted": 198,
  "updated": 23,
  "pages": 1,
  "error": null
}
```

| Field | Meaning |
|---|---|
| `status` | `"success"` or `"failed"` |
| `source` | Always `"usgs"` |
| `fetched` | Total events returned by USGS after client-side type filter |
| `inserted` | New rows added to `seismic_events` |
| `updated` | Existing rows refreshed (includes overlapping TexNet events) |
| `pages` | Number of API pages requested (1 page = up to 5000 events) |
| `error` | Populated only on failure |

---

### `GET /api/v1/seismic/events`

Returns paginated events from the `seismic_events` table, shared with TexNet. Use the `source` parameter to filter by catalog.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int ≥ 1 | `1` | Page number (1-based) |
| `page_size` | int 1–1000 | `50` | Records per page |
| `source` | string | `null` | `"usgs"` or `"texnet"` — omit for combined view |
| `county` | string | `null` | Delaware county name — applies only to TexNet events (USGS has no county) |
| `min_magnitude` | float | `null` | Filter to events at or above this magnitude |

**Response example (USGS event):**
```json
{
  "total": 221,
  "page": 1,
  "page_size": 3,
  "items": [
    {
      "source": "usgs",
      "event_id": "tx2025iqwk",
      "magnitude": 5.4,
      "mag_type": "ml",
      "latitude": 31.647,
      "longitude": -104.458,
      "depth": 7.54,
      "event_type": "earthquake",
      "event_date": "2025-05-04T01:47:05.553000",
      "evaluation_status": "reviewed",
      "rms": 0.1,
      "phase_count": null,
      "region_name": null,
      "county_name": null,
      "station_count": null,
      "place": "59 km S of Whites City, New Mexico",
      "title": "M 5.4 - 59 km S of Whites City, New Mexico",
      "alternate_ids": ",us7000pwzs,tx2025iqwk,usauto7000pwzs,",
      "gap": 69.0
    }
  ]
}
```

Results are ordered by `event_date DESC` (most recent first).

---

## Testing

### 1. Verify the USGS FDSN endpoint is reachable

```bash
curl -s "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&minlatitude=28.5&maxlatitude=32.5&minlongitude=-105.5&maxlongitude=-102.5&minmagnitude=4&eventtype=earthquake&limit=1" \
  | python3 -m json.tool
```

Expected: a GeoJSON `FeatureCollection` with at least one feature. Each feature has `properties.mag`, `properties.time`, `geometry.coordinates`, etc. If you get an HTTP 400, check that no parameter name is misspelled.

---

### 2. Probe with explicit parameters before a full fetch

```bash
curl -s "https://earthquake.usgs.gov/fdsnws/event/1/query" \
  -G \
  --data-urlencode "format=geojson" \
  --data-urlencode "minlatitude=28.5" \
  --data-urlencode "maxlatitude=32.5" \
  --data-urlencode "minlongitude=-105.5" \
  --data-urlencode "maxlongitude=-102.5" \
  --data-urlencode "starttime=2000-01-01" \
  --data-urlencode "minmagnitude=3.5" \
  --data-urlencode "eventtype=earthquake" \
  --data-urlencode "orderby=time-asc" \
  --data-urlencode "limit=5" \
  --data-urlencode "offset=1" \
  | python3 -m json.tool
```

Expected: 5 features, ordered oldest-first. Confirm `properties.type = "earthquake"` and `geometry.coordinates` has three elements `[lon, lat, depth_km]`.

---

### 3. Confirm `starttime` is required — demo the 30-day default trap

```bash
# Without starttime — returns only last 30 days
curl -s "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&minlatitude=28.5&maxlatitude=32.5&minlongitude=-105.5&maxlongitude=-102.5&minmagnitude=3.5&eventtype=earthquake&limit=100" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('features (30-day):', len(d['features']))"

# With starttime — returns full history
curl -s "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&minlatitude=28.5&maxlatitude=32.5&minlongitude=-105.5&maxlongitude=-102.5&starttime=2000-01-01&minmagnitude=3.5&eventtype=earthquake&limit=100" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('features (2000-present):', len(d['features']))"
```

Expected: the first number is much smaller (single digits at M≥3.5). The second is much larger (hundreds). This is why `USGS_START_TIME` defaults to `2000-01-01`.

---

### 4. Start the API and trigger the first fetch

```bash
# Terminal 1
python main.py

# Terminal 2 — fetch all events (uses USGS_MIN_MAGNITUDE default = 1.5)
curl -s -X POST "http://localhost:8000/api/v1/seismic/usgs/fetch" | python3 -m json.tool
```

Expected response:
```json
{
  "status": "success",
  "source": "usgs",
  "fetched": 221,
  "inserted": 198,
  "updated": 23,
  "pages": 1,
  "error": null
}
```

`fetched` and `inserted` counts will vary over time as USGS adds new events or revises existing ones. `updated` counts events whose IDs already existed in the database (from a previous USGS or TexNet fetch).

---

### 5. Verify idempotency

```bash
curl -s -X POST "http://localhost:8000/api/v1/seismic/usgs/fetch" | python3 -m json.tool
```

Expected: `"inserted": 0`, `"updated": <same count as first run>`. If you see new inserts, USGS has published new events since the last fetch.

---

### 6. Query USGS-only events

```bash
# All USGS events, most recent first
curl -s "http://localhost:8000/api/v1/seismic/events?source=usgs&page_size=5" | python3 -m json.tool

# Significant events only
curl -s "http://localhost:8000/api/v1/seismic/events?source=usgs&min_magnitude=4" | python3 -m json.tool

# Combine with TexNet in one view (omit source)
curl -s "http://localhost:8000/api/v1/seismic/events?min_magnitude=4&page_size=10" | python3 -m json.tool
```

---

### 7. Cross-catalog reconciliation via `alternate_ids`

The `alternate_ids` column stores the raw `properties.ids` string from USGS (e.g. `",us7000s8ml,tx2025iqwk,"`). An event that appears in both catalogs will have a TexNet-style ID (`texnet*` or `tx*`) in its USGS `alternate_ids`. Query the database directly to find overlapping events:

```bash
sqlite3 fracfocus_data/fracfocus.db ".mode column" ".headers on" \
  "SELECT event_id, magnitude, event_date, alternate_ids \
   FROM seismic_events \
   WHERE source = 'usgs' AND alternate_ids LIKE '%texnet%' \
   ORDER BY magnitude DESC LIMIT 10;"
```

To find events where a USGS row and a TexNet row describe the same earthquake:

```bash
sqlite3 fracfocus_data/fracfocus.db \
  "SELECT u.event_id as usgs_id, t.event_id as texnet_id, u.magnitude, u.event_date, u.alternate_ids \
   FROM seismic_events u \
   JOIN seismic_events t ON u.alternate_ids LIKE '%' || t.event_id || '%' \
   WHERE u.source = 'usgs' AND t.source = 'texnet' \
   ORDER BY u.event_date DESC LIMIT 10;"
```

---

### 8. Fetch with a higher magnitude threshold

```bash
# Only M≥2.5 — faster, smaller result set
curl -s -X POST "http://localhost:8000/api/v1/seismic/usgs/fetch?min_magnitude=2.5" | python3 -m json.tool
```

---

### 9. Inspect source breakdown in the database

```bash
sqlite3 fracfocus_data/fracfocus.db ".mode column" ".headers on" \
  "SELECT source, COUNT(*) as events, \
          MIN(magnitude) as min_mag, MAX(magnitude) as max_mag, \
          MIN(event_date) as earliest, MAX(event_date) as latest \
   FROM seismic_events \
   GROUP BY source \
   ORDER BY source;"
```

Expected output shows two rows: `texnet` (post-2017, Delaware counties) and `usgs` (from `USGS_START_TIME`, full bbox).

---

### 10. Interactive Swagger UI

Open `http://localhost:8000/docs` and expand the **seismic** section. Both `/texnet/fetch` and `/usgs/fetch` are available. The `/events` endpoint's `source` parameter lets you filter between catalogs without switching endpoints.

---

## Known Behaviors and Edge Cases

**`starttime` is mandatory for historical coverage.** Without `USGS_START_TIME`, the USGS FDSN API silently defaults to the last 30 days. At M≥3.5, this returns only a handful of events. The setting defaults to `2000-01-01`, which captures the full pre-TexNet baseline period.

**USGS `updated` count includes TexNet overlaps.** When you re-run `/usgs/fetch` after `/texnet/fetch`, events that share the same `event_id` across both catalogs get updated. The `updated` count reflects all rows whose `event_id` already existed in the table — this is expected and correct.

**`county_name` is always null for USGS events.** USGS does not return county-level attribution. If you filter `GET /events?county=reeves`, USGS events are excluded. Use `?source=usgs` alone, or combine with `?min_magnitude=...` to work with USGS-only data.

**Event IDs use network prefixes.** USGS returns its own primary ID for each event (`tx*`, `us*`, `nc*`, etc.). These differ from TexNet IDs for the same physical earthquake. The `alternate_ids` field is the linkage — it contains every known network ID for the event, comma-delimited, including TexNet IDs where applicable.

**`evaluation_status` values differ from TexNet.** TexNet uses `"final"` / `"preliminary"`. USGS uses `"reviewed"` / `"automatic"`. Both map to the same `evaluation_status` column — treat them as separate namespaces when filtering. A USGS `"reviewed"` event is roughly equivalent to TexNet `"final"` in terms of catalog quality.

**Pagination stop condition.** The USGS FDSN API does not return a total event count in GeoJSON format (the `metadata.count` field is absent). Pagination continues until `len(features) < page_size`. With the default `page_size=5000`, most Delaware Basin fetches complete in one page. If FracFocus is ever used with `min_magnitude=1.0`, the result set could exceed 5000 and trigger multi-page fetches.

**USGS magnitude revisions.** USGS routinely revises event magnitudes and locations as more waveform data is processed. Re-running `/usgs/fetch` updates all existing rows via the upsert, so revised values are picked up automatically. This is why `updated` is non-zero on re-fetch even without new events.

**Depth is in km.** `geometry.coordinates[2]` from USGS is depth in kilometres, consistent with TexNet. Both are stored as-is in `seismic_events.depth`.
