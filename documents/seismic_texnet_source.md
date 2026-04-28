# TexNet Earthquake Catalog — Source Documentation

**Data bucket:** Seismic  
**Source:** TexNet (Bureau of Economic Geology, UT Austin)  
**Access method:** ArcGIS REST Feature Service  
**Layer URL:** `https://maps.texnet.beg.utexas.edu/arcgis/rest/services/catalog/catalog_all/MapServer/0`

---

## Overview

TexNet is Texas's dedicated seismic monitoring network, operated by the Bureau of Economic Geology at UT Austin. It is the authoritative catalog for induced seismicity in the Delaware Basin — the primary geographic focus of this PoC.

**Why this source is primary:**
- Provides the highest-density station coverage for the Delaware Basin, yielding better event location precision than national catalogs (USGS) for Texas events.
- Events are linked directly to the same geographic area as the RRC injection and FracFocus datasets, enabling event-to-well attribution.
- The ArcGIS REST service supports spatial bounding-box queries and server-side magnitude filtering, so only relevant records are transferred.
- Research that directly underpins the PoC (Aziz Zanjani et al. 2024, *The Seismic Record*) used TexNet alongside FracFocus for southern Delaware Basin attribution.

**Delaware trim:** The bounding box (`lat 28.5–32.5°N`, `lon -105.5–-102.5°W`) intentionally overlaps adjacent counties like Presidio and Crane. A county-name post-filter keeps only the six Delaware Basin counties: **Culberson, Reeves, Loving, Ward, Winkler, Pecos**.

---

## Architecture

```
POST /api/v1/seismic/texnet/fetch
        │
        ▼
  TexNetService.fetch_delaware_events()     ← app/services/texnet_service.py
        │   Paginates ArcGIS REST via resultOffset / exceededTransferLimit
        │   Filters EventType = 'earthquake' server-side
        │   Trims to Delaware counties client-side
        ▼
  SeismicEventRepository.upsert_many()      ← app/repositories/seismic_repository.py
        │   Upserts keyed on event_id (idempotent — safe to re-run)
        ▼
  seismic_events  (SQLite table)            ← app/models/seismic_event.py

GET /api/v1/seismic/events
        │
        ▼
  SeismicEventRepository.get_paginated()
        │   Supports county + min_magnitude filters
        ▼
  JSON response  →  SeismicEventListResponse
```

---

## Configuration

All values have sensible defaults. Override via `.env` or environment variables.

| Variable | Default | Description |
|---|---|---|
| `TEXNET_REST_URL` | `https://maps.texnet.beg.utexas.edu/…/MapServer/0` | ArcGIS REST layer endpoint (no trailing `/query`) |
| `TEXNET_BBOX_MIN_LAT` | `28.5` | South boundary of the Delaware Basin bounding box |
| `TEXNET_BBOX_MAX_LAT` | `32.5` | North boundary |
| `TEXNET_BBOX_MIN_LON` | `-105.5` | West boundary |
| `TEXNET_BBOX_MAX_LON` | `-102.5` | East boundary |
| `REQUEST_TIMEOUT` | `120` | HTTP timeout in seconds (shared with FracFocus sync) |

**Changing the bounding box** does not require code changes — adjust the four `TEXNET_BBOX_*` variables and re-fetch.

---

## Data Model

Table: `seismic_events` (SQLAlchemy ORM, `app/models/seismic_event.py`)

| Column | Type | Source field | PoC use |
|---|---|---|---|
| `event_id` | TEXT (unique) | `EventId` | Primary key for all cross-source joins and evidence lineage |
| `magnitude` | FLOAT | `Magnitude` | Event ranking, filtering, attribution context |
| `mag_type` | TEXT | `MagType` | Preserves ML / Mw distinction for scientific integrity |
| `latitude` | FLOAT | `Latitude` | Map display, spatial join to nearby SWD / frac wells |
| `longitude` | FLOAT | `Longitude` | Map display, spatial join |
| `depth` | FLOAT | `Depth` | Injection depth vs. event depth comparison (core attribution) |
| `phase_count` | INTEGER | `PhaseCount` | Event solution confidence — shown in the evidence view |
| `event_type` | TEXT | `EventType` | Always `'earthquake'` after server-side filter |
| `region_name` | TEXT | `RegionName` | Human-readable location label for the event card |
| `event_date` | DATETIME (UTC) | `Event_Date` | Core timestamp — drives attribution time windows and all temporal joins |
| `evaluation_status` | TEXT | `EvaluationStatus` | `final` / `preliminary` — prefer `final` events for demo |
| `county_name` | TEXT | `CountyName` | Delaware trim field (uppercased on write) |
| `rms` | FLOAT | `RMS` | Waveform fit quality — confidence framing in the result card |
| `station_count` | INTEGER | `StationCount` | Station support — key confidence signal in the evidence view |
| `fetched_at` | DATETIME | *(internal)* | Timestamp of last successful upsert |

**Non-stored fields** (available in the raw ArcGIS response but excluded per the data plan): `EarthquakeId`, `Agency`, `Method`, `LatitudeError`, `LongitudeError`, `DepthSurface`, `Add_Date`, `DepthUncertainty`, `EvaluationMode`, `UpdateTime`, focal mechanism fields, `MagnitudeCategory`, `MaximumDistance`, `CityName`, `MomentMagnitude`.

---

## API Endpoints

### `POST /api/v1/seismic/texnet/fetch`

Triggers a fetch from TexNet and upserts the results into the local database. Safe to call repeatedly — events are matched on `event_id` so re-fetching updates existing rows rather than duplicating them.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `min_magnitude` | float | `null` | If set, only events at or above this magnitude are fetched and stored. The filter is applied server-side in the ArcGIS `WHERE` clause. |

**Response (`TexNetFetchResult`):**
```json
{
  "status": "success",
  "fetched": 204,
  "inserted": 0,
  "updated": 204,
  "pages": 0,
  "error": null
}
```

| Field | Meaning |
|---|---|
| `status` | `"success"` or `"failed"` |
| `fetched` | Events returned by TexNet after Delaware county trim |
| `inserted` | New rows added to `seismic_events` |
| `updated` | Existing rows refreshed |
| `error` | Populated only on failure |

---

### `GET /api/v1/seismic/events`

Returns paginated seismic events from the local database.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int ≥ 1 | `1` | Page number (1-based) |
| `page_size` | int 1–1000 | `50` | Records per page |
| `county` | string | `null` | Filter by Delaware county name — case-insensitive exact match (e.g. `reeves`, `LOVING`) |
| `min_magnitude` | float | `null` | Filter to events at or above this magnitude |

**Response (`SeismicEventListResponse`):**
```json
{
  "total": 204,
  "page": 1,
  "page_size": 50,
  "items": [
    {
      "event_id": "texnet2021zmyq",
      "magnitude": 3.5,
      "mag_type": "ML",
      "latitude": 31.992,
      "longitude": -103.871,
      "depth": 7.52,
      "phase_count": 21,
      "event_type": "earthquake",
      "region_name": "Western Texas",
      "event_date": "2021-12-29T20:28:44.185000",
      "evaluation_status": "final",
      "county_name": "LOVING",
      "rms": 0.134,
      "station_count": 14
    }
  ]
}
```

Results are ordered by `event_date DESC` (most recent first).

---

## Testing

### 1. Verify the TexNet ArcGIS endpoint is reachable

```bash
curl -s "https://maps.texnet.beg.utexas.edu/arcgis/rest/services/catalog/catalog_all/MapServer/0?f=json" \
  | python3 -m json.tool | grep '"name"'
```

Expected: a JSON response with `"name": "TexNet Earthquake Catalog"` (or similar). If you get a connection error the endpoint URL may have changed — check the [TexNet catalog portal](https://catalog.texnet.beg.utexas.edu/).

---

### 2. Probe a single page before a full fetch

```bash
curl -s "https://maps.texnet.beg.utexas.edu/arcgis/rest/services/catalog/catalog_all/MapServer/0/query" \
  -G \
  --data-urlencode "where=EventType = 'earthquake' AND Magnitude >= 4" \
  --data-urlencode "geometry=-105.5,28.5,-102.5,32.5" \
  --data-urlencode "geometryType=esriGeometryEnvelope" \
  --data-urlencode "inSR=4326" \
  --data-urlencode "spatialRel=esriSpatialRelIntersects" \
  --data-urlencode "outFields=EventId,Magnitude,CountyName,Event_Date,EvaluationStatus" \
  --data-urlencode "returnGeometry=false" \
  --data-urlencode "resultRecordCount=5" \
  --data-urlencode "f=json" \
  | python3 -m json.tool
```

Expected: a JSON object with a `features` array. Each feature has an `attributes` object. If `exceededTransferLimit` is `true`, pagination would be needed.

---

### 3. Start the API and trigger the first full fetch

```bash
# Terminal 1 — start the server
python main.py

# Terminal 2 — trigger fetch (no magnitude filter — returns all Delaware events)
curl -s -X POST "http://localhost:8000/api/v1/seismic/texnet/fetch" | python3 -m json.tool
```

Expected response:
```json
{
  "status": "success",
  "fetched": 204,
  "inserted": 204,
  "updated": 0,
  "pages": 0,
  "error": null
}
```

`fetched` will vary as TexNet adds new reviewed events. On a first run, `inserted` should equal `fetched` and `updated` should be `0`.

---

### 4. Verify idempotency (re-run should be all updates, no inserts)

```bash
curl -s -X POST "http://localhost:8000/api/v1/seismic/texnet/fetch" | python3 -m json.tool
```

Expected: `"inserted": 0`, `"updated": <same count as first run>`. If you see new inserts on the second run, new events were published by TexNet between the two calls.

---

### 5. Query the stored events

```bash
# All events, most recent first
curl -s "http://localhost:8000/api/v1/seismic/events?page_size=5" | python3 -m json.tool

# Filter to a single county
curl -s "http://localhost:8000/api/v1/seismic/events?county=reeves&page_size=5" | python3 -m json.tool

# Filter to significant events only
curl -s "http://localhost:8000/api/v1/seismic/events?min_magnitude=4" | python3 -m json.tool

# Combine filters
curl -s "http://localhost:8000/api/v1/seismic/events?county=culberson&min_magnitude=3&page=1&page_size=10" | python3 -m json.tool
```

Valid county values: `culberson`, `reeves`, `loving`, `ward`, `winkler`, `pecos` (case-insensitive).

---

### 6. Fetch with a magnitude threshold

```bash
# Only M≥2.5 events — reduces storage, faster fetch
curl -s -X POST "http://localhost:8000/api/v1/seismic/texnet/fetch?min_magnitude=2.5" | python3 -m json.tool
```

The `min_magnitude` filter is applied server-side in the ArcGIS `WHERE` clause, so only matching events are transferred.

---

### 7. Interactive Swagger UI

Open `http://localhost:8000/docs` and expand the **seismic** section. You can execute both endpoints directly from the browser — useful for exploring query parameter combinations without constructing curl commands.

---

### 8. Inspect the database directly

```bash
sqlite3 fracfocus_data/fracfocus.db ".mode column" ".headers on" \
  "SELECT county_name, COUNT(*) as events, MIN(magnitude) as min_mag, MAX(magnitude) as max_mag \
   FROM seismic_events \
   GROUP BY county_name \
   ORDER BY events DESC;"
```

Expected output shows event counts and magnitude ranges per Delaware county.

```bash
# Verify no non-Delaware counties slipped through the trim
sqlite3 fracfocus_data/fracfocus.db \
  "SELECT DISTINCT county_name FROM seismic_events ORDER BY county_name;"
```

Expected: only `CULBERSON`, `LOVING`, `PECOS`, `REEVES`, `UNKNOWN`, `WARD`, `WINKLER` (or a subset depending on available events). `UNKNOWN` appears when TexNet does not resolve a county for an event within the bbox — these are kept rather than dropped, since the coordinates themselves are within the basin.

---

## Known Behaviors and Edge Cases

**County name casing:** TexNet returns county names in mixed case (`Culberson`) or uppercase (`CULBERSON`) inconsistently across catalog versions. The service normalises to uppercase before the county trim check and before writing to the database.

**`UNKNOWN` county:** Some events within the bounding box have `CountyName = 'Unknown'`. The current trim keeps these because their coordinates fall inside the Delaware Basin bbox. If you want only confirmed-county events, filter with `?county=reeves` etc. at query time.

**`exceededTransferLimit` pagination:** The ArcGIS server caps responses at its `maxRecordCount` setting (typically 2000). If TexNet publishes enough events that a single page is full, the service automatically advances `resultOffset` and fetches subsequent pages. This is transparent — `fetch_delaware_events()` always returns the complete dataset.

**Date handling:** TexNet returns `Event_Date` as Unix epoch milliseconds (e.g. `1560822497235`). This is converted to a naive UTC datetime before storage. All `event_date` values in the database are UTC.

**Re-fetching after a TexNet catalog revision:** TexNet occasionally re-processes and revises events (location, magnitude, status). Re-running `POST /texnet/fetch` updates all existing rows via the upsert, so revised values are picked up automatically.
