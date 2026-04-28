# EarthScope (IRIS) FDSN Station Metadata

## Overview

EarthScope (formerly IRIS — Incorporated Research Institutions for Seismology) operates the
FDSN Station Web Service, which exposes metadata for every seismic station in their federated
network. For the Delaware Basin PoC, station metadata answers a key contextual question: **which
seismometers were (or are) monitoring the area during the injection and fracturing activity we're
correlating?**

Station records include location, elevation, operational dates, and the network that owns the
instrument. These fields enable proximity analysis ("is this station within 50 km of injection
well X?") and coverage-gap detection ("were any stations active during event cluster Y?").

---

## API

| Property | Value |
|---|---|
| Base URL | `https://service.iris.edu/fdsnws/station/1/query` |
| Format | `format=text` — pipe-delimited plain text, one station per line |
| Level | `level=station` — one row per station (not per channel) |
| Auth | None (public) |
| Pagination | None — all results returned in a single response |

### Request parameters used

| Param | Source | Notes |
|---|---|---|
| `format` | hardcoded `text` | Easier to parse than StationXML; avoids `lxml` dependency |
| `level` | hardcoded `station` | Channel-level would return orders of magnitude more rows |
| `minlatitude` | `TEXNET_BBOX_MIN_LAT` | Shared Delaware Basin bounding box |
| `maxlatitude` | `TEXNET_BBOX_MAX_LAT` | |
| `minlongitude` | `TEXNET_BBOX_MIN_LON` | |
| `maxlongitude` | `TEXNET_BBOX_MAX_LON` | |

### Response format (text)

```
#Network | Station | Latitude | Longitude | Elevation | SiteName | StartTime | EndTime
TX|ALPN|30.364800|-103.578300|1350.0|Alpine, TX, USA|2017-01-01T00:00:00.0000|
SC|CBET|32.420300|-103.879000|1042.0|Carlsbad East Tower, NM|2015-12-06T00:00:00.0000|
```

- `EndTime` is empty when the station is currently operational.
- Datetime strings carry microseconds: `%Y-%m-%dT%H:%M:%S.%f`.
- The same `{Network}.{Station}` can appear in multiple rows if the station was reinstalled or
  reconfigured (different epoch). The service keeps the **last occurrence** (latest epoch).

---

## Data model — `iris_stations` table

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `network_station` | Text UNIQUE | `"{network}.{station_code}"` — natural key for upsert |
| `network` | Text | e.g. `TX`, `N4`, `IU`, `SC` |
| `station_code` | Text | e.g. `ALPN`, `WB03` |
| `latitude` | Float | WGS-84 decimal degrees |
| `longitude` | Float | WGS-84 decimal degrees |
| `elevation` | Float | Metres above sea level |
| `site_name` | Text | Human-readable location label |
| `start_time` | DateTime | When the station became operational (UTC) |
| `end_time` | DateTime | When decommissioned; **null = currently operational** |
| `fetched_at` | DateTime | UTC timestamp of last successful fetch |

Schema migrations follow the `_ensure_iris_station_columns()` pattern — any column added to the
ORM model but absent from the live table is added via `ALTER TABLE ADD COLUMN` on startup.

---

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `IRIS_STATION_URL` | `https://service.iris.edu/fdsnws/station/1/query` | Overridable in `.env` |

The Delaware Basin bounding box is shared with the TexNet and USGS pipelines:
`TEXNET_BBOX_MIN_LAT / MAX_LAT / MIN_LON / MAX_LON` (28.5–32.5°N, -105.5–-102.5°W).

---

## Implementation files

| File | Role |
|---|---|
| `app/models/iris_station.py` | SQLAlchemy ORM model (`iris_stations` table) |
| `app/services/iris_service.py` | HTTP fetch + text parsing + epoch deduplication |
| `app/repositories/iris_repository.py` | Upsert keyed on `network_station`, paginated query |
| `app/schemas/iris.py` | Pydantic `IRISStationOut`, `IRISFetchResult`, `IRISStationListResponse` |
| `app/api/v1/endpoints/iris.py` | FastAPI routes (mounted under `/seismic`) |

---

## API endpoints

### `POST /api/v1/seismic/iris/stations/fetch`

Triggers a fresh pull from the EarthScope FDSN Station API. Idempotent — re-running updates
existing rows rather than inserting duplicates.

**Request:** no body, no required query params.

**Response:**
```json
{
  "status": "success",
  "source": "iris",
  "fetched": 330,
  "inserted": 330,
  "updated": 0
}
```

On re-run: `inserted=0`, `updated=330`.

---

### `GET /api/v1/seismic/iris/stations`

Returns paginated station list.

**Query params:**

| Param | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | 1-based page number |
| `page_size` | int | 50 | Max 1000 |
| `network` | string | — | Filter by network code (e.g. `TX`, `N4`). Case-insensitive. |
| `active_only` | bool | false | When true, only stations with `end_time = null` |

**Response:**
```json
{
  "total": 330,
  "page": 1,
  "page_size": 50,
  "items": [
    {
      "network_station": "1A.WP03",
      "network": "1A",
      "station_code": "WP03",
      "latitude": 32.491699,
      "longitude": -104.515503,
      "elevation": 1272.0,
      "site_name": "Carlsbad, New Mexico, USA",
      "start_time": "2013-07-09T00:00:00",
      "end_time": "2013-07-11T23:59:59"
    }
  ]
}
```

---

## Testing

### Step 1 — Start the API

```bash
python main.py
```

### Step 2 — Trigger the fetch

```bash
curl -s -X POST http://localhost:8000/api/v1/seismic/iris/stations/fetch | python -m json.tool
```

Expected: `"status": "success"`, `fetched` ≈ 330, `inserted` = `fetched` on first run.

### Step 3 — Idempotency check

Run the same POST again. Expect `inserted=0`, `updated` = prior `inserted` value.

### Step 4 — List all stations

```bash
curl -s "http://localhost:8000/api/v1/seismic/iris/stations?page_size=5" | python -m json.tool
```

Verify `total` matches the `fetched` count, `items` has 5 entries.

### Step 5 — Filter by network

```bash
curl -s "http://localhost:8000/api/v1/seismic/iris/stations?network=TX" | python -m json.tool
```

All returned items should have `"network": "TX"`.

### Step 6 — Filter by active-only

```bash
curl -s "http://localhost:8000/api/v1/seismic/iris/stations?active_only=true" | python -m json.tool
```

All returned items should have `"end_time": null`. `total` will be less than the full count.

### Step 7 — Verify DB directly

```bash
python - <<'EOF'
import sqlite3
con = sqlite3.connect("fracfocus_data/fracfocus.db")
cur = con.cursor()
cur.execute("SELECT COUNT(*) FROM iris_stations")
print("total:", cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM iris_stations WHERE end_time IS NULL")
print("currently operational:", cur.fetchone()[0])
cur.execute("SELECT DISTINCT network FROM iris_stations ORDER BY network")
print("networks:", [r[0] for r in cur.fetchall()])
con.close()
EOF
```

### Step 8 — Cross-catalog proximity query (correlation use case)

Identify which IRIS stations are within ~50 km of a seismic event:

```sql
SELECT
    s.event_id,
    s.event_date,
    s.magnitude,
    s.latitude  AS ev_lat,
    s.longitude AS ev_lon,
    i.network_station,
    i.site_name,
    i.latitude  AS st_lat,
    i.longitude AS st_lon,
    -- Rough Euclidean distance in degrees (good enough for PoC filtering)
    ROUND(
        SQRT(
            (s.latitude  - i.latitude)  * (s.latitude  - i.latitude) +
            (s.longitude - i.longitude) * (s.longitude - i.longitude)
        ), 3
    ) AS dist_deg
FROM seismic_events s
JOIN iris_stations i
    ON ABS(s.latitude  - i.latitude)  < 0.5   -- ~55 km
    AND ABS(s.longitude - i.longitude) < 0.5
    AND (i.end_time IS NULL OR i.end_time >= s.event_date)
    AND (i.start_time IS NULL OR i.start_time <= s.event_date)
WHERE s.magnitude >= 3.0
ORDER BY s.event_date DESC, dist_deg
LIMIT 50;
```

---

## Known edge cases

| Situation | Behaviour |
|---|---|
| Same `{network}.{station}` with multiple epochs | Service keeps the last row (latest epoch) — earlier operational periods are silently discarded |
| Station currently active | `end_time = null` in both the raw response and the DB |
| IRIS returns HTTP 204 (no stations in bbox) | `response.raise_for_status()` passes; loop produces zero rows; upsert is a no-op |
| Datetime with microseconds (`...T00:00:00.0000`) | Parsed by the `%Y-%m-%dT%H:%M:%S.%f` format string (tried before the plain-second format) |
