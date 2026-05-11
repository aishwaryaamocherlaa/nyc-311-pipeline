"""
NYC 311 Dashboard - Flask Server
=================================
Author: Aishwarya Mocherla

Serves an interactive HTML dashboard backed by the PostgreSQL `nyc_311`
database. Exposes one JSON endpoint per chart

Caters to: 
Phase 4: Serving & Presentation Layer
Load your Gold layer aggregations into a local PostgreSQL instance to serve as the
backend for your visualization.
Interactive HTML Dashboard:
Build an interactive HTML dashboard that communicates the insights derived from the
database. This should not be a collection of static charts.
• Dynamic Interactivity: The dashboard must respond to user input. If a user
changes a parameter, the visualizations must update to represent that specific
slice of data.
• Required Filter Elements:
o By Council District: Filter all charts to show data for one or multiple
specific districts.
o By Complaint Type: Drill down into specific issues (e.g., "Potholes" vs.
"Illegal Dumping").
o By Time Horizon: Adjust the view based on the creation date of the
requests
• Visual Elements: Include a mix of spatial, categorical, and temporal charts (e.g.,
maps, bar charts, and time-series graphs).
"""

import os
from datetime import datetime
from flask import Flask, jsonify, render_template, request
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

PG_URL = (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER')}:"
    f"{os.getenv('POSTGRES_PASSWORD')}@{os.getenv('POSTGRES_HOST')}:"
    f"{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
)
engine = create_engine(PG_URL, pool_pre_ping=True)

app = Flask(__name__)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def parse_filters():
    """Read filters from the request's query string."""
    districts = request.args.getlist("district")
    districts = [int(d) for d in districts if d.strip()]
    complaint_types = request.args.getlist("complaint_type")
    complaint_types = [ct for ct in complaint_types if ct.strip()]
    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    return {
        "districts": districts,
        "complaint_types": complaint_types,
        "date_from": date_from,
        "date_to": date_to,
    }


def fetch(sql: str, params: dict = None):
    """Run a parameterized SQL query and return list of dicts."""
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result]


@app.route("/")
def index():
    """Serve the dashboard HTML page."""
    return render_template("index.html")




@app.route("/api/options")
def options():
    """Return all dropdown options (districts, complaint types, date range)."""
    districts = fetch(
        'SELECT DISTINCT council_district FROM g1_district_performance '
        'ORDER BY council_district'
    )
    complaint_types = fetch(
        'SELECT complaint_type FROM g2_complaint_distribution '
        'ORDER BY citywide_volume DESC LIMIT 50'
    )
    dates = fetch(
        'SELECT MIN(created_date_partition) AS min_date, '
        'MAX(created_date_partition) AS max_date FROM g4_temporal_trends'
    )
    return jsonify({
        "districts": [r["council_district"] for r in districts],
        "complaint_types": [r["complaint_type"] for r in complaint_types],
        "date_min": str(dates[0]["min_date"]),
        "date_max": str(dates[0]["max_date"]),
    })



@app.route("/api/kpi")
def kpi():
    """Top-of-page KPI tiles. Filtered."""
    f = parse_filters()
    where = []
    params = {}
    if f["districts"]:
        where.append("council_district = ANY(:districts)")
        params["districts"] = f["districts"]
    where_sql = " WHERE " + " AND ".join(where) if where else ""

    row = fetch(
        f'SELECT SUM(total_volume) AS total_volume, '
        f'SUM(closed_volume) AS closed_volume, '
        f'AVG(avg_resolution_hours) AS avg_resolution_hours '
        f'FROM g1_district_performance{where_sql}',
        params,
    )[0]

    total = int(row["total_volume"] or 0)
    closed = int(row["closed_volume"] or 0)
    return jsonify({
        "total_volume": total,
        "closure_rate": round(closed / total * 100, 2) if total else 0,
        "avg_resolution_hours": round(float(row["avg_resolution_hours"] or 0), 2),
        "overdue_open": fetch(
            'SELECT volume FROM g10_open_backlog_aging '
            "WHERE age_bucket IN ('7-30d', '30-90d', '>90d')"
        ),
    })


@app.route("/api/g11_map")
def g11_map():
    """G11 - district bubbles for the spatial map."""
    f = parse_filters()
    where = []
    params = {}
    if f["districts"]:
        where.append("council_district = ANY(:districts)")
        params["districts"] = f["districts"]
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    return jsonify(fetch(
        f'SELECT council_district, primary_community_board, avg_latitude, '
        f'avg_longitude, total_volume, avg_resolution_hours, closure_rate, '
        f'top_complaint_type FROM g11_geo_density{where_sql} '
        f'ORDER BY total_volume DESC',
        params,
    ))


@app.route("/api/g4_temporal")
def g4_temporal():
    """G4 - daily volume time series. Filtered by date range."""
    f = parse_filters()
    where = []
    params = {}
    if f["date_from"]:
        where.append("created_date_partition >= :date_from")
        params["date_from"] = f["date_from"]
    if f["date_to"]:
        where.append("created_date_partition <= :date_to")
        params["date_to"] = f["date_to"]
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    return jsonify(fetch(
        f'SELECT created_date_partition, total_volume, closed_volume, '
        f'closure_rate, avg_resolution_hours, day_of_week '
        f'FROM g4_temporal_trends{where_sql} ORDER BY created_date_partition',
        params,
    ))


@app.route("/api/g7_heatmap")
def g7_heatmap():
    """G7 - hour x day-of-week heatmap. Not filterable (city-wide pattern)."""
    return jsonify(fetch(
        'SELECT created_day_of_week, created_hour, volume '
        'FROM g7_hourly_heatmap ORDER BY created_day_of_week, created_hour'
    ))


@app.route("/api/g1_districts")
def g1_districts():
    """G1 - district performance bar."""
    f = parse_filters()
    where = []
    params = {}
    if f["districts"]:
        where.append("council_district = ANY(:districts)")
        params["districts"] = f["districts"]
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    return jsonify(fetch(
        f'SELECT council_district, primary_community_board, total_volume, '
        f'closed_volume, avg_resolution_hours, median_resolution_hours '
        f'FROM g1_district_performance{where_sql} '
        f'ORDER BY total_volume DESC LIMIT 20',
        params,
    ))


@app.route("/api/g5_bottleneck")
def g5_bottleneck():
    """G5 - bottleneck districts."""
    f = parse_filters()
    where = []
    params = {}
    if f["districts"]:
        where.append("council_district = ANY(:districts)")
        params["districts"] = f["districts"]
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    return jsonify(fetch(
        f'SELECT council_district, primary_community_board, open_volume, '
        f'closed_volume, open_to_closed_ratio, citywide_ratio, exceeds_citywide '
        f'FROM g5_bottleneck_analysis{where_sql} '
        f'ORDER BY open_to_closed_ratio DESC LIMIT 20',
        params,
    ))


@app.route("/api/g2_complaints")
def g2_complaints():
    """G2 - top complaint types citywide."""
    f = parse_filters()
    where = []
    params = {}
    if f["complaint_types"]:
        where.append("complaint_type = ANY(:complaint_types)")
        params["complaint_types"] = f["complaint_types"]
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    return jsonify(fetch(
        f'SELECT complaint_type, citywide_volume, top_district, top_district_volume '
        f'FROM g2_complaint_distribution{where_sql} '
        f'ORDER BY citywide_volume DESC LIMIT 15',
        params,
    ))


@app.route("/api/g9_hotspots")
def g9_hotspots():
    """G9 - top zips per complaint type."""
    f = parse_filters()
    where = []
    params = {}
    if f["complaint_types"]:
        where.append("complaint_type = ANY(:complaint_types)")
        params["complaint_types"] = f["complaint_types"]
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    return jsonify(fetch(
        f'SELECT complaint_type, incident_zip, volume, rank '
        f'FROM g9_hotspot_zips{where_sql} '
        f'ORDER BY complaint_type, rank LIMIT 50',
        params,
    ))


@app.route("/api/g3_agencies")
def g3_agencies():
    return jsonify(fetch(
        'SELECT agency, agency_name, total_volume, closed_volume, '
        'closure_rate, avg_resolution_hours, median_resolution_hours '
        'FROM g3_agency_efficiency ORDER BY total_volume DESC LIMIT 15'
    ))


@app.route("/api/g6_sla")
def g6_sla():
    return jsonify(fetch(
        'SELECT agency, agency_name, closed_with_sla, within_sla_count, '
        'sla_compliance_pct FROM g6_sla_compliance '
        'ORDER BY sla_compliance_pct DESC'
    ))


@app.route("/api/g10_aging")
def g10_aging():
    return jsonify(fetch(
        'SELECT age_bucket, volume FROM g10_open_backlog_aging '
        "ORDER BY CASE age_bucket "
        "WHEN '<1d' THEN 1 WHEN '1-7d' THEN 2 WHEN '7-30d' THEN 3 "
        "WHEN '30-90d' THEN 4 WHEN '>90d' THEN 5 ELSE 6 END"
    ))


@app.route("/api/g8_channels")
def g8_channels():
    f = parse_filters()
    where = []
    params = {}
    if f["districts"]:
        where.append("council_district = ANY(:districts)")
        params["districts"] = f["districts"]
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    return jsonify(fetch(
        f'SELECT council_district, primary_community_board, '
        f'open_data_channel_type, volume '
        f'FROM g8_channel_mix_by_district{where_sql} '
        f'ORDER BY council_district, volume DESC',
        params,
    ))


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)