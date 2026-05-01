# SWD (Saltwater Disposal) — Source Documentation

**Data bucket:** SWD (Injection)
**Sources:** Texas Railroad Commission (RRC) — 3 sub-sources
**Status:** NOT YET IMPLEMENTED

---

## Why SWD Matters for This PoC

The PoC attribution workflow is:

```
Selected seismic event → attribution analysis → interpretable result
```

SWD is the **primary driver candidate** in the Delaware Basin. Per Smye et al. 2024:
- >4 billion barrels injected into shallow Delaware formations
- Documented pore pressure increases up to **5 MPa** tied to SWD
- Pressure-front migration from injection sites to seismic hypocenters confirmed

Without SWD data, the attribution engine cannot answer: *"Is this earthquake caused by nearby saltwater disposal?"*

**Full causal chain the PoC must demonstrate:**
```
Oil/gas production → produced water → SWD injection → pore pressure increase → seismicity
```

All three RRC sub-sources below map to one part of this chain.

---

## Delaware Basin County Filter

All RRC sources are statewide. Filter to Delaware Basin after ingest using county code:

| County | RRC Code |
|--------|----------|
| Culberson | 023 |
| Reeves | 137 |
| Loving | 169 |
| Ward | 185 |
| Winkler | 207 |
| Pecos | 121 |

---

## Source 1 — RRC UIC Inventory & Permit

**What it is:** The well registry for every injection/disposal well in Texas. Every H-10 monthly monitoring record is keyed against a UIC control number from this inventory.

**Why needed:** Establishes which wells are authorized, at what depth, of what type (Class II = saltwater disposal), and where they are located. Provides the static context (location, depth, operator) that the monthly H-10 data is joined to.

### Access

| Method | URL | Format | Notes |
|--------|-----|--------|-------|
| **Texas Open Data Portal (recommended)** | `https://data.texas.gov/resource/givw-z9t4.json` | JSON / CSV (Socrata API) | Already has lat/lon — no coordinate resolution needed |
| Direct CSV download | `https://data.texas.gov/api/views/givw-z9t4/rows.csv?accessType=DOWNLOAD` | CSV | ~126K rows, one-shot download |
| RRC bulk (legacy, hard) | `https://mft.rrc.texas.gov/link/d2438c05-b42f-45a8-b0c6-edceb0912767` | Fixed-width ASCII, 14-segment Magnetic Tape format | Requires byte-position parser per UIC manual |
| UIC Magnetic Tape Manual | `https://www.rrc.texas.gov/media/v3onmigl/uic_manual_uia010_3116.pdf` | PDF | Schema for the legacy bulk file |
| UIC web query | `https://webapps2.rrc.texas.gov/EWA/uicQueryAction.do` | Web form | Validation only — not for bulk ingest |
| RRC GIS Viewer | `https://gis.rrc.texas.gov/GISViewer/` | Spatial viewer | Spatial validation |
| Injection-Storage resources | `https://www.rrc.texas.gov/oil-and-gas/applications-and-permits/injection-storage-permits/resources/` | Info page | Background and filing guidance |

**Login required:** No. Free access.
**Record count:** ~126,441 wells statewide.
**Update frequency:** Monthly (by 3rd workday).

### Why Open Data Portal over the legacy bulk file

The legacy MFT bulk download is a fixed-width EBCDIC/ASCII file with 14 different segment types. Parsing requires byte-position tables from the UIC manual and EBCDIC-to-ASCII conversion. The Open Data Portal version (`givw-z9t4`) exposes the same data as clean JSON/CSV via Socrata REST — **and crucially, already includes `LATITUDE_NAD83` / `LONGITUDE_NAD83`**. The data plan notes these coordinates as "derived" because in the legacy file they are absent — the only location is a legal description (`UIC-LOCATION`). The Open Data Portal has already resolved them.

### Key Columns (from Open Data Portal)

| Column | Data Plan Field | ★ Key | PoC Use |
|--------|-----------------|-------|---------|
| `UIC_NUMBER` | UIC-CNTL-NO | ★ YES | Primary UIC control identifier — join key to H-10 monthly records |
| `OIL_GAS_CODE` | UIC-O-G-TYPE | ★ YES | Oil/gas permit context, QA in evidence view |
| `DISTRICT_CODE` | UIC-DIST | No | RRC district metadata |
| `LEASE_NUMBER` | UIC-LEASE-ID | No | Lease traceability |
| `WELL_NO_DISPLAY` | UIC-WELL-NO | ★ YES | Well number for record matching across RRC datasets |
| `OPERATOR_NUMBER` | UIC-OPER | ★ YES | Operator ID — evidence display |
| `API_NO` | UIC-API-NO | ★ YES | API number — cross-join to FracFocus and RRC Wellbore |
| `FIELD_NUMBER` | UIC-FIELD-NO | No | Field context |
| `UIC_TYPE_INJECTION` | UIC-TYPE-INJ | ★ YES | Injection type — distinguishes SWD from EOR |
| `ACTIVATED_FLAG` | UIC-STATUS (proxy) | No | Active/inactive — exclude decommissioned |
| `MAX_LIQ_INJ_PRESSURE` | UIC-MAX-INJ-PRESSURE | No | Static permit pressure limit |
| `TOP_INJ_ZONE` | — | No | Top of injection zone (ft) |
| `BOT_INJ_ZONE` | UIC-DPTH-BOT-OF-TOP-ZONE | ★ YES | Bottom of injection zone — depth classification (shallow 0.5–3 km vs. deep 4–6 km) |
| `LATITUDE_NAD83` | Latitude (derived) | ★ YES | Map display and spatial join to seismic events |
| `LONGITUDE_NAD83` | Longitude (derived) | ★ YES | Map display and spatial join |
| `BBL_VOL_INJ` | — | No | Cumulative barrels from permit — context only |
| `W14_DATE` | UIC-W14-NO (proxy) | ★ YES | Disposal into nonproductive zone authority |
| `H1_DATE` | UIC-H1-NO (proxy) | ★ YES | H-1 injection/disposal authority |

**Note:** `UIC_INJ_SW` (saltwater injection flag Y/N) is in the legacy bulk file but does not appear separately in the Open Data Portal schema — `UIC_TYPE_INJECTION` code distinguishes well types instead. Class II = saltwater disposal.

### Target Table

```
swd_wells_delaware
Primary key: uic_number
```

---

## Source 2 — RRC H-10 Monthly Injection Monitoring

**What it is:** Each authorized injection well must file a monthly H-10 report with the RRC. These records contain the injected volume (barrels) and pressure (PSIG) for every calendar month of operation.

**Why needed:** Monthly volume and pressure are the **central SWD variables** for the attribution engine. Cumulative injection load and lag time from injection start to event date are the two derived fields the engine needs. This is the most important SWD sub-source for the PoC.

### Access

| Method | URL | Format | Notes |
|--------|-----|--------|-------|
| **Texas Open Data Portal (recommended)** | `https://data.texas.gov/resource/qq2j-f2zm.json` | JSON / CSV (Socrata API) | 20.8M rows, paginated via Socrata |
| Direct CSV download | `https://data.texas.gov/api/views/qq2j-f2zm/rows.csv?accessType=DOWNLOAD` | CSV | Large file — full dataset |
| H-10 web query | `http://webapps.rrc.texas.gov/H10/h10PublicMain.do` | Web form | Validation / spot checks only |
| H-10 annual report page | `https://www.rrc.texas.gov/oil-and-gas/applications-and-permits/injection-storage-permits/injection-reporting/annual-report/` | Info page | Filing obligations and due dates — NOT a download |
| H-10 EDI specification | `https://www.rrc.texas.gov/media/e0dgox0c/h10-edi-specifications.pdf` | PDF | EDI schema operators use to file |
| Within UIC bulk file | `https://mft.rrc.texas.gov/link/d2438c05-b42f-45a8-b0c6-edceb0912767` | Fixed-width, Table 04 segment | Same data, harder format |

**Login required:** No. Free access.
**Record count:** ~20,832,219 rows (one row per well per month).
**Update frequency:** Monthly.

### Key Columns (from Open Data Portal)

| Column | Data Plan Field | ★ Key | PoC Use |
|--------|-----------------|-------|---------|
| `UIC_NO` | MN-H10 (linked via UIC key) | ★ YES | Join to `swd_wells_delaware.uic_number` |
| `FORMATTED_DATE` | MN-H10-YEAR + MN-H10-MONTH | ★ YES | Monthly key for temporal alignment to seismic event |
| `INJ_PRESS_AVG` | MN-H10-AVG-INJ-PRESSURE | ★ YES | Core pressure field — pore pressure proxy |
| `INJ_PRESS_MAX` | MN-H10-MAX-INJ-PRESSURE | ★ YES | Max pressure — high-pressure context in evidence |
| `VOL_LIQ` | MN-H10-TOTAL-VOL-BBL | ★ YES | Monthly injected volume (barrels) — core attribution variable |
| `VOL_GAS` | MN-H10-TOTAL-VOL-MCF | No | Gas volume — rarely relevant for SWD |
| `TOZ` | — | No | Top of injection zone (ft) |
| `BOZ` | — | No | Bottom of injection zone (ft) |
| `COMMERCIAL` | — | No | Commercial disposal flag |
| `MOST_RECENT_RECORD` | — | No | Flag for most recent entry |

### Derived Fields (computed at query time, not stored)

| Derived Field | Data Plan Name | How to Compute |
|---------------|----------------|----------------|
| Cumulative volume | Cumulative Volume (derived) | `SUM(VOL_LIQ)` for all months from first injection date up to seismic event date |
| Lag time | Days Since Injection Start (derived) | `event_date - MIN(FORMATTED_DATE)` for that well |

### Target Table

```
swd_monthly_monitor_delaware
Primary key: uic_no + formatted_date
```

---

## Source 3 — RRC PDQ Production Data Query

**What it is:** Lease-level monthly oil, gas, and condensate production across Texas. Jan 1993 to present.

**Why needed:** Production volume is the upstream generator of produced water — the feedstock for SWD injection. High production predicts high disposal volumes. Including this completes the causal chain in the evidence object: *production → produced water → SWD → pressure → seismicity*.

### Access

| Method | URL | Format | Notes |
|--------|-----|--------|-------|
| **PDQ CSV dump (recommended)** | `https://mft.rrc.texas.gov/link/1f5ddb8d-329a-4459-b7f8-177b4f5ee60d` | CSV | Last Saturday of each month |
| PDQ web query | `https://webapps2.rrc.texas.gov/EWA/ewaPdqMain.do` | Web form | No export — display only |
| PDQ FAQs | `https://www.rrc.texas.gov/about-us/faqs/oil-gas-faq/production-data-query-system-faqs/` | Info page | Important: web query cannot export |
| PDQ user manual | `https://www.rrc.texas.gov/media/50ypu2cg/pdq-dump-user-manual.pdf` | PDF | Schema for the CSV dump |

**Login required:** No. Free access.
**Coverage:** January 1993 to present.
**Update frequency:** Last Saturday of each month.
**Reporting lag:** 2 months — the most recent 2 months are incomplete. Account for this in temporal alignment logic.

### Key Columns

| Column | ★ Key | PoC Use |
|--------|-------|---------|
| `lease_no` / `LEASE_ID` | ★ YES | Lease identifier — join key to well cluster |
| `API_NO` | ★ YES | API number — cross-join to UIC records and FracFocus |
| `oper_no` / `OPERATOR_NO` | ★ YES | Operator — evidence display |
| `county` / `COUNTY_NO` | ★ YES | Delaware trim field |
| `cycle_year` / `CYCLE_YEAR` | ★ YES | Reporting year for temporal alignment |
| `cycle_month` / `CYCLE_MONTH` | ★ YES | Reporting month |
| `oil_prod_vol` / `OIL_PROD_VOL` | ★ YES | Monthly oil (BBL) — generates produced water |
| `gas_prod_vol` / `GAS_PROD_VOL` | ★ YES | Monthly gas (MCF) — contextual |
| `water_prod_vol` / `WATER_PROD_VOL` | ★ YES | Monthly produced water (BBL) — direct SWD feedstock proxy; not always present |
| `condensate_prod_vol` / `COND_PROD_VOL` | No | Less relevant |
| `lease_name` / `LEASE_NAME` | No | Display label only |

### Target Table

```
production_delaware
Primary key: lease_id + cycle_year + cycle_month
```

---

## How the Three Sources Connect

```
swd_wells_delaware          (UIC Inventory)
        │  uic_number (PK)
        │  api_no
        │  latitude, longitude
        │  bot_inj_zone (depth)
        │
        ├──── swd_monthly_monitor_delaware   (H-10 Monthly)
        │         uic_no → FK to swd_wells
        │         formatted_date
        │         vol_liq (barrels/month)
        │         inj_press_avg, inj_press_max
        │
        └──── production_delaware            (PDQ Production)
                  api_no → FK via api_no
                  cycle_year, cycle_month
                  oil_prod_vol → produced water → SWD feedstock
```

The attribution engine joins against `seismic_events` using:
- **Spatial join:** `swd_wells.latitude/longitude` vs. `seismic_events.latitude/longitude` (configurable radius, e.g. 20 km)
- **Temporal join:** `swd_monthly_monitor.formatted_date` vs. `seismic_events.event_date` (configurable lookback window, e.g. 12 months prior)

---

## Final PoC Output Table

```
event_context_snapshot
Primary key: event_id + run_timestamp

Contains (per selected seismic event):
  - The seismic event itself
  - Nearby SWD wells (within radius) + their monthly volumes
  - Nearby frac jobs (FracFocus) within time window
  - Cumulative injection load and lag-time derived fields
  - Attribution result: likely driver + confidence + evidence
```

---

## Architecture (planned — not yet implemented)

```
POST /api/v1/swd/uic/fetch
        │
        ▼
  UICService.fetch_delaware_wells()
        │   Socrata API pagination (data.texas.gov/resource/givw-z9t4.json)
        │   Filter: county_code IN (023, 137, 169, 185, 207, 121)
        ▼
  SWDRepository.upsert_many_wells()
        │   Upsert on uic_number
        ▼
  swd_wells_delaware  table

POST /api/v1/swd/h10/fetch
        │
        ▼
  H10Service.fetch_delaware_monitoring()
        │   Socrata API pagination (data.texas.gov/resource/qq2j-f2zm.json)
        │   Join filter: uic_no IN (SELECT uic_number FROM swd_wells_delaware)
        ▼
  SWDRepository.upsert_many_monitoring()
        │   Upsert on uic_no + formatted_date
        ▼
  swd_monthly_monitor_delaware  table

POST /api/v1/swd/pdq/fetch
        │
        ▼
  PDQService.fetch_delaware_production()
        │   CSV dump from mft.rrc.texas.gov
        │   Filter: county_no IN Delaware codes
        ▼
  SWDRepository.upsert_many_production()
        ▼
  production_delaware  table
```

---

## What Is NOT Yet Known / Needs to Be Decided

These are open questions that will affect implementation:

| Question | Why It Matters |
|----------|---------------|
| What radius (km) defines "nearby" for spatial join? | Determines which SWD wells are included in attribution |
| What lookback window (months) before event date? | Determines how many H-10 records are pulled per event |
| Does the existing attribution engine expect a specific input format? | May require adapting the SWD data shape to match engine expectations |
| Should `production_delaware` be implemented first or after UIC+H10? | PDQ adds causal chain context but UIC+H10 are more directly used in attribution |
| Socrata app token needed? | Without a token, Socrata limits anonymous requests to ~1000 rows/request — pagination is still possible but slower; a free app token removes the throttle |

---

## What You Need to Provide to Start Implementation

Before SWD implementation can begin, provide or confirm:

1. **Socrata app token** (optional but recommended)
   - Register free at: `https://data.texas.gov/profile/app_tokens`
   - Removes rate limiting on `data.texas.gov` API calls
   - Without it: pagination still works, just slightly slower

2. **Search radius** — how many km from a seismic event to look for nearby SWD wells (e.g. 20 km, 50 km)

3. **Lookback window** — how many months of H-10 records before the event date to include (e.g. 12 months, 24 months)

4. **Attribution engine input contract** — if there is an existing Permian/scientific codebase that runs the attribution, what format does it expect SWD data in?
   - The SoW mentions an *"existing Permian scientific codebase"* — if this exists, its input schema should drive our data model

5. **Priority order** — implement UIC + H-10 first (directly used in attribution) or PDQ too (causal chain context)?
