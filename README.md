# NYC 311 Public Works Data Pipeline

An end-to-end data engineering project that ingests, transforms, and serves
NYC 311 Service Request data using a Medallion Architecture (Bronze → Silver
→Gold), with a final interactive HTML dashboard for municipal leaders.


## Project Goal

Provide municipal leaders with a tool to monitor service efficiency and
identify infrastructure bottlenecks across NYC, by processing the live
[311 Service Requests dataset](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2020-to-Present/erm2-nwe9/about_data)
into business-ready aggregations and a responsive dashboard.



## Architecture


NYC Open Data API (SODA3) ->   BRONZE: Raw JSON, faithful to source. Phase 1 (Paginated, audit-stamped) -> SILVER: Cleaned, validated, typed. Phase 2 (Partitioned Parquet) -> GOLD: Business-ready aggregations. Phase 3 (Loaded into PostgreSQL) -> DASHBOARD: Interactive HTML dashboard. ← Phase 4

## Project Structure

nyc_311_pipeline/
  data/
    bronze/        # Raw JSON ingestion output + manifest
    silver/        # Cleaned Parquet (Phase 2)
  gold/          # Aggregated analytical tables (Phase 3)
  dashboard/         # HTML dashboard application 
  logs/              # Per-run structured logs
  src/               # Pipeline source code (one script per layer)
    ingest_bronze.py
  tests/             # Validation and sanity checks
    test_api.py
    test_fetch_page.py
    validate_bronze.py
  .env               # Local secrets (all gitignored)
  .gitignore
  requirements.txt
  README.md

## Phase 1: Data Ingestion (Bronze Layer) ✅

### What it does
Programmatically fetches NYC 311 Service Requests from the SODA3 API
and persists raw JSON to `data/bronze/`, alongside an auditable manifest.

### Run it
```bash
python src/ingest_bronze.py
```

### Validate it
```bash
python tests/validate_bronze.py
```

### Assignment requirement coverage

| Requirement                                                         | Where it's handled                                                    |
|---------------------------------------------------------------------|-----------------------------------------------------------------------|
| Programmatic ingestion via SODA3 API in Python                      | `src/ingest_bronze.py` → `fetch_page()` (uses `requests`)             |
| Identify correct API endpoint and query structure from documentation| `API_BASE_URL` constant + SoQL `$where/$order/$limit/$offset` params  |
| App Token authentication                                            | `.env` → loaded via `python-dotenv` → sent as `X-App-Token` header    |
| Temporal filter: records on or after April 1, 2026                  | `CUTOFF_DATE` constant → applied via `$where` clause                  |
| Store raw output in a Bronze data lake                              | Per-page JSON files in `data/bronze/`                                 |

### Data engineering best practices

- **JSON over CSV**: preserves nested structures (e.g. the `location` Point
  object) and source data types. Aligns with Bronze's principle of capturing
  source data with maximum fidelity, deferring all type coercion to Silver.

- **Page-per-file storage** (`bronze_page_NNN.json`): smaller files are
  easier to inspect, support resumability if a run is interrupted, and
  partition cleanly for parallel reads in Silver.

- **Deterministic pagination order** (`$order=created_date ASC, unique_key ASC`):
  guarantees no overlaps and no gaps between paginated calls, eliminating
  silent data loss, a class of bug typical of naive pagination implementations.

- **Manifest as a downstream contract**: `manifest.json` records the cutoff
  date, page count, total records, and timestamps for each ingestion run.
  Provides auditability and is consumed by Silver for completeness checks.

- **Exponential backoff with selective retry**: only retry transient failures
  (HTTP 429, 5xx, network errors). Authentication and malformed-request
  failures (HTTP 4xx) raise immediately retrying won't fix them.

- **Structured logging to terminal and file**: every run produces a
  timestamped `.log` in `logs/`, providing a permanent record alongside
  real-time progress visibility.

### Evaluation criteria coverage (Phase 1 contributions)

- **Documentation Research**: Endpoint, dataset ID, and SoQL query syntax
  identified independently from the NYC Open Data + Socrata docs.
- **Pipeline Architecture**: Bronze layer is fully self-contained;
  outputs land only in `data/bronze/`. The manifest establishes a clean
  contract for the Silver layer.
- **Data Integrity**: Source-faithful storage, deterministic ordering,
  manifest cross-checked against records on disk via `validate_bronze.py`.
- **Optimization**: Maximum page size (50K) to minimize round-trips,
  `requests.Session` reuses connections across paginated calls.

### Phase 1 results (latest run)

- Total records ingested: **385,649**
- Pages persisted: **8** (`bronze_page_001.json` … `bronze_page_008.json`)
- Date range covered: 2026-04-01 → 2026-05-09
- Wall-clock duration: ~117 seconds

---

## Phase 2: Data Transformation (Silver Layer) 

### What it does
Reads all Bronze JSON page files, applies cleaning and validation,
derives analytical fields, and writes the result as partitioned Parquet
to `data/silver/`. Produces an auditable manifest alongside.

### Run it
```bash
python src/transform_silver.py
```

### Validate it
```bash
python tests/validate_silver.py
```

### Assignment requirement coverage
Schema consistency : handled in `COLUMNS_TO_KEEP`: explicit 16-column source schema        
Handling null values in critical fields : handled in `CRITICAL_FIELDS` dropped + logged; non-critical filled with placeholders
Standardizing categorical labels : handled in Borough uppercased; channel uppercased; all strings stripped        
Derived temporal metrics (resolution time) : handled in `resolution_time_hours` + `resolution_time_bucket`                   
Final processed data stored as partitioned Parquet files : handled in `data/silver/created_date_partition=YYYY-MM-DD/part-0.parquet`      
Partitioning strategy optimizes downstream analytical queries : handled in Daily partitioning (39 folders, ~10K rows each, aligned with F3 Time Horizon filter)

### Bes Practices and Optimization Strategy

- **Daily partitioning by `created_date_partition`**: 39 partitions for 5.5 weeks of data,
  with ~10,000 rows each. Chosen over `borough` (only 5 partitions, severely unbalanced)
  and `complaint_type` (~200 partitions, many trivially small). Daily partitioning
  aligns directly with the required F3 Time Horizon filter - partition pruning makes
  filtered date-range queries dramatically faster.

- **JSON → Parquet conversion**: 385,649 records went from ~250 MB of raw JSON to
  ~20 MB of columnar Parquet (~12× compression). Columnar storage means downstream
  queries can read only the columns they need.

- **9 derived analytical columns**: `resolution_time_hours`, `resolution_time_bucket`,
  `is_closed`, `is_overdue`, `is_data_quality_issue`, `created_hour`, `created_day_of_week`,
  `created_is_weekend`, `created_date_partition`. Pre-computed in Silver so Gold
  aggregations don't recompute them and the dashboard can filter directly.

- **Tiered data integrity policy**: critical fields (`unique_key`, `created_date`)
  trigger row drops; other nulls are filled with explicit placeholders so `GROUP BY`
  behaves predictably. Logically impossible values (`closed_date < created_date`)
  are kept on the row but flagged with `is_data_quality_issue=True` and have
  `resolution_time_hours` nulled out - preserving the row for volume analysis
  but excluding it from resolution-time averages.

- **Categorical label standardization**: borough uppercased ("BROOKLYN"),
  `open_data_channel_type` uppercased ("PHONE", "ONLINE", "MOBILE") per NYC's
  data dictionary, but `complaint_type` left mixed-case to match NYC's canonical
  labels ("Noise - Residential", not "NOISE - RESIDENTIAL"). Stripping whitespace
  is applied universally to dedupe near-duplicates.

### Evaluation criteria coverage (Phase 2 contributions)

- **Pipeline Architecture** - Silver is fully self-contained; reads only from
  `data/bronze/`, writes only to `data/silver/`. The manifest establishes a
  clean contract for the Gold layer.
- **Data Integrity** - Tiered handling policy (drop / fill / flag-and-null);
  manifest records every category count; validator cross-checks against the
  Silver dataset.
- **Optimization** - Daily partitioning enables partition pruning on the most
  common dashboard filter; columnar Parquet compresses 12× and enables
  column-selective reads.

### Phase 2 results (latest run)

- Input records: **385,649** (from Bronze)
- Output records: **385,649** (zero data loss)
- Rows dropped (null critical fields): **0**
- Rows flagged (data quality issue): **55** (0.014%)
- Rows closed: **325,708** (84.5%)
- Rows overdue and open: **502** (active SLA violations)
- Partitions: **39** (daily, April 1 → May 9)
- Output size: **20.34 MB** (~12× smaller than Bronze JSON)
- Wall-clock duration: **~26 seconds**

## Phase 3: Analytical Aggregations (Gold Layer) 

### What it does
Reads the partitioned Silver dataset, produces business-ready aggregation tables, and writes each as a single Parquet file in `data/gold/`, alongside a manifest summarizing the run.

### Run it
```bash
python src/aggregate_gold.py
```

### Tables 
**Required tables:**

- G1 `g1_district_performance.parquet` : Average resolution time + total volume per Council District (`council_district`, `resolution_time_hours`, `is_closed`)
- G2`g2_complaint_distribution.parquet` : Most frequent complaint types citywide + top district per type (`complaint_type`, `council_district`)
- G3`g3_agency_efficiency.parquet` : Volume, avg resolution, and closure rate per responding agency (`agency`, `agency_name`, `resolution_time_hours`, `is_closed`)
- G4`g4_temporal_trends.parquet` : Daily volume and closure stats: identifies peak demand periods (`created_date_partition`, `is_closed`, `resolution_time_hours`)
- G5`g5_bottleneck_analysis.parquet` : Districts where open-to-closed ratio exceeds the citywide average (`council_district`, `is_closed`)

**Additional tables:**

- G6`g6_sla_compliance.parquet` : % of closed cases resolved within the agency's own due date (`agency`, `due_date`, `closed_date`, `is_closed`)
- G7`g7_hourly_heatmap.parquet` : Volume by hour-of-day × day-of-week - staffing/demand pattern (`created_hour`, `created_day_of_week`)
- G8`g8_channel_mix_by_district.parquet` : How citizens in each district report issues - phone / online / mobile (`council_district`, `open_data_channel_type`)
- G9`g9_hotspot_zips.parquet` : Top 5 ZIP codes by volume for each complaint type - sub-district hotspots (`incident_zip`, `complaint_type`)
- G10`g10_open_backlog_aging.parquet` : Aging distribution of still-open tickets - `<1d`, `1-7d`, `7-30d`, `30-90d`, `>90d` (`is_closed`, `created_date`)

### Additional Tables Descriptions

The assignment notes that requirements are *"including but not limited to"*. The 5 bonus tables target other operational questions a city operations team would want answered:

- **G6 SLA Compliance**: distinguishes agencies that close tickets *quickly* (already in G3) from agencies that close tickets *within their own promised window*. Different question, different ops decision.
- **G7 Hourly Heatmap**: averages hide rhythms. Sunday-morning noise spikes vs. Tuesday-afternoon street complaints have different staffing implications.
- **G8 Channel Mix**: surfaces underutilization. If District 14 has 90% phone reports while the city average is 40%, that district may be underserved by the mobile app and underrepresented in real-time response.
- **G9 Hotspot ZIPs**: sub-district granularity. A council district can contain 5–10 ZIP codes; surfacing the highest-density ZIP per complaint type drives precision targeting.
- **G10 Open Backlog Aging** : actionable bottleneck view. G5 flags districts with bad ratios; G10 surfaces *which specific tickets* have been festering the longest, regardless of district.

### Assignment requirement coverage

District Performance (avg resolution + volume per Council District): G1 - `build_g1_district_performance()` 
Complaint Distribution (citywide vs. district-specific): G2 - `build_g2_complaint_distribution()`
Agency Efficiency (avg response across agencies): G3 - `build_g3_agency_efficiency()`
Temporal Trends (daily/weekly volume + peak periods): G4 - `build_g4_temporal_trends()`
Bottleneck Analysis (districts > citywide open/closed ratio): G5 - `build_g5_bottleneck_analysis()` 

### Best Practices & Optimization strategies

- **One Parquet file per Gold table** rather than partitioning. Gold tables are small (mostly < 200 rows), pre-aggregated, and queried as a whole. Partitioning here would add file-system overhead without query benefit.
- **All-Parquet output** rather than CSV. Same columnar / typed-storage advantages as Silver; downstream Postgres load reads them efficiently with `pandas.read_parquet`.
- **Manifest captures per-table stats** (row count, column count, KB on disk). A reviewer can audit every table without opening any of them.

### Phase 3 results (latest run)

- 10 Gold tables built
- Source Silver record count: 385,649
- Total Gold output size: ~50 KB (massive compression vs. 20 MB Silver - each Gold table is a summary, not the underlying data)
- Wall-clock duration: ~5 seconds

### Validate it
```bash
python tests/validate_postgres.py
```
(Postgres validator below also confirms each Gold table was loaded correctly.)

---

## Phase 4 part 1: PostgreSQL Load ✅

### What it does
Reads each Gold Parquet file, ensures the local PostgreSQL database `nyc_311` exists, writes each Gold table into it via `pandas.to_sql()`, and creates indexes on the columns the dashboard filters on most heavily.

### Run it
```bash
python src/load_postgres.py
```

### Validate it
```bash
python tests/validate_postgres.py
```

### Assignment requirement coverage
"Load your Gold layer aggregations into a local PostgreSQL instance": `load_gold_tables()` -  one Postgres table per Gold Parquet file
Evaluation: "Effective use of Parquet partitioning **and PostgreSQL indexing**": `create_indexes()` - 13 indexes across 10 tables on common filter columns 

### Index strategy
- `g1_district_performance` : `council_district` (F1 District filter)
- `g2_complaint_distribution` : `complaint_type` (F2 Complaint Type filter)
- `g3_agency_efficiency` : `agency` (Agency drilldown)
- `g4_temporal_trends` : `created_date_partition` (F3 Time Horizon filter)
- `g5_bottleneck_analysis` : `council_district` (F1 + drill into worst-performing districts)
- `g6_sla_compliance` : `agency` (Agency drilldown)
- `g7_hourly_heatmap` : `created_day_of_week`, `created_hour` (Composite for heatmap cell lookups)
- `g8_channel_mix_by_district` : `council_district`, `open_data_channel_type` (F1 + channel drilldown)
- `g9_hotspot_zips` : `complaint_type`, `incident_zip` (F2 + ZIP-level drilldown)
- `g10_open_backlog_aging` : `age_bucket` (Bucket-level filter)

13 indexes total. Each lines up with a specific dashboard query pattern.

### Phase 4 (load) results

- Database created: `nyc_311`
- 10 tables loaded (matching the 10 Gold Parquet files)
- 13 indexes created on filter columns
- Wall-clock duration: ~3 seconds

---

## Data Quality & Known Limitations

The Silver layer enforces a tiered data-handling policy (hard-drop for structural fields, fill-with-placeholder for non-critical nulls, flag-and-null for logically impossible values). However, a few real-world data quirks remain visible in the dashboard and are documented here for transparency rather than silently normalized:

### 1. Inconsistent casing across complaint types

Some agencies (NYPD, DOT, DSNY) use title-case complaint labels (`"Noise - Residential"`); HPD uses uppercase (`"HEAT/HOT WATER"`, `"UNSANITARY CONDITION"`). This is the canonical convention each agency uses internally and is not a typo. Silver preserves source fidelity and does not editorially normalize these; the dashboard render layer can apply display-side casing if a single visual style is preferred.

### 2. Sparse `due_date` field

Several agencies (notably NYPD) do not populate `due_date` for their service requests. G6 (SLA Compliance) therefore covers only agencies that populate the field. The metric is correct but the agency coverage is partial 

### 3. The QC techniques applied - and what would be added in a longer project

For Silver-layer cleaning, the following QC techniques were used to surface inconsistencies before deciding on a handling policy:

- `value_counts()` on every categorical column surfaced casing variants (`"Unspecified"` vs `"UNSPECIFIED"`), leading/trailing whitespace patterns, and unexpected categories.
- Schema-dtype inspection confirmed which fields were ingested as strings and needed casting.
- Manual cross-reference against NYC's published data dictionary.

In a longer project a dedicated `profile_silver.py` script would run systematically across every column (null counts, min/max for numerics, cardinality + top values for categoricals, cross-field consistency checks like `closed_date >= created_date`, partition-level row-count distribution) and write a profiling report. This was scoped out for the take-home, but it's the natural next addition.


## Phase 4: Serving & Dashboard

### What it does

A Flask web application served at `http://127.0.0.1:5000` that renders an interactive HTML dashboard backed entirely by the local PostgreSQL `nyc_311` database. The dashboard exposes the three required filters (Council District, Complaint Type, Time Horizon) which dynamically update all charts via parameterized SQL queries against the indexed Gold tables.

### Run it

```bash
python dashboard/app.py
```

Then open **http://127.0.0.1:5000** in any modern browser.

### Tech stack

- **Flask** - minimal Python web server, exposes one JSON endpoint per chart
- **SQLAlchemy + psycopg2** - parameterized, indexed queries against the Postgres backend
- **Plotly.js** - interactive client-side charts (zoom, pan, hover, legend toggling)
- **Vanilla HTML + CSS + JS** - no framework overhead, easy for a reviewer to run

### Assignment requirement coverage

| Requirement                                                                                                | Where it's handled                                                  |
|-------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|
| "Load your Gold layer aggregations into a local PostgreSQL instance"                                        | `src/load_postgres.py` populates `nyc_311`; the dashboard reads only from there |
| "Interactive HTML dashboard … not a collection of static charts"                                            | Plotly-based; every chart re-fetches & re-renders when filters change |
| "Dynamic Interactivity: visualizations must update when a user changes a parameter"                          | `dashboard.js` re-issues filtered API calls and re-renders all charts |
| Filter: **By Council District** (one or multiple)                                                            | `<select multiple>` → `ANY(:districts)` parameter in SQL `WHERE` clauses |
| Filter: **By Complaint Type** (drill into specific issues)                                                   | `<select multiple>` → applied to G2 and G9 endpoints                |
| Filter: **By Time Horizon** (date range of creation)                                                         | Two `<input type=date>` controls → applied to G4 endpoint           |
| **Spatial chart**                                                                                             | G11 District Density Map (Plotly Mapbox + OpenStreetMap tiles)      |
| **Categorical charts**                                                                                        | G1, G2, G3, G5, G6, G8, G9, G10 - bar/stacked-bar/horizontal-bar     |
| **Temporal charts**                                                                                           | G4 daily line chart + G7 hour × weekday heatmap                     |

### Dashboard layout

A single scrollable page in seven horizontal rows:

1. **Filter bar** - district multi-select, complaint-type multi-select, date-from + date-to inputs, Apply / Clear buttons
2. **KPI row** - total volume, closure rate, avg resolution hours, aging open (>7 days)
3. **Spatial** - G11 District Density Map (full-width)
4. **Temporal** - G4 daily volume trend · G7 hour × weekday heatmap
5. **Geographic ranking** - G1 district performance · G5 bottleneck districts
6. **Complaint mix** - G2 top complaint types · G9 hotspot ZIPs
7. **Agency view** - G3 agency volume + closure · G6 SLA compliance
8. **Operations** - G10 backlog aging · G8 channel mix by district

### Evaluation criteria coverage (Phase 4 contributions)

- **User Experience (fluidity & responsiveness)** - All charts re-render in well under a second on a filter change, because they query indexed Postgres tables that are already pre-aggregated. The map uses Plotly Mapbox over OpenStreetMap so panning and zooming feel native; the heatmap is a single matrix render so it pans smoothly. The Apply / Clear filter buttons are explicit so users batch changes; date-range inputs auto-apply on change for one-handed exploration. Bar/line/heatmap interactions (hover for tooltip, click legend to toggle a series) come free from Plotly.
- **Pipeline Architecture** - The dashboard is a strict consumer; it issues no transformations, no aggregations, and no data cleaning. Every chart corresponds to exactly one Gold table loaded into one indexed Postgres table. Separation of concerns is enforced by file layout: pipeline code in `src/`, dashboard code in `dashboard/`, no shared mutable state.
- **Optimization** - Filters compose into `WHERE … = ANY(:array)` clauses against indexed columns; Postgres' query planner uses index scans rather than full table scans for every filter combination.

### Design decisions

- **Server-side filtering, not client-side.** Plotly *can* filter in the browser, but that would require shipping the full table to every user. Server-side SQL filtering means the wire payload is always exactly the rows the chart will render - faster, scales to bigger datasets.
- **Indexed columns drive filter design.** Every filter parameter (`council_district`, `complaint_type`, `created_date_partition`) corresponds to a column we explicitly indexed during the Postgres load. The dashboard's responsiveness is a direct payoff of the indexing strategy.
- **Plotly Mapbox over a heavier mapping library (e.g. Folium / Leaflet integrations).** Plotly Mapbox renders client-side, handles zoom/pan smoothly without extra dependencies, and stays consistent with the chart library used everywhere else on the page.
- **District centroids (not raw points) on the map.** With ~385K incident lat/longs, plotting them all in the browser would be slow and visually noisy. G11 pre-computes the centroid of each council district along with its volume and avg resolution time, so the map shows one informative bubble per district - clean and fast.
- **Plain-language one-liner under each chart title.** A municipal-leader audience shouldn't have to infer what to look for. Each panel explicitly states what the chart surfaces and what the leader should notice.

### Known limitations

- **Some aggregations are not filter-aware.** G3 (Agency Efficiency), G6 (SLA Compliance), G7 (Hourly Heatmap) and G10 (Backlog Aging) reflect citywide patterns; they don't drill by district or complaint type without re-aggregating from Silver. The dashboard treats these as global context panels and leaves them static. Adding per-filter versions would require either re-aggregating on every filter change (slow) or pre-computing each filter combination (storage-heavy).
- **Map shows district centroids, not individual incidents.** Useful for high-level pattern recognition; not useful for street-level diagnostics. Adding a "zoom in to see individual reports" mode would require a heavier Silver query path on demand.
- **Casing inconsistencies in complaint type labels remain visible** (`"HEAT/HOT WATER"` vs. `"Noise - Residential"`). This is intentional - see "Data Quality & Known Limitations" section above for the rationale.