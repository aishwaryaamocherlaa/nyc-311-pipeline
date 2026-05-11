"""
Gold Layer Aggregation 

Reads partitioned Silver Parquet, produces business-ready aggregations,
and writes each as its own Parquet file to data/gold/.

Phase 3: Analytical Aggregations (Gold Layer)
The Gold layer should contain highly specialized, business-ready datasets. You are
required to generate multiple aggregations to drive the final dashboard, including:
• District Performance: Average resolution time and total volume per Council
District.
• Complaint Distribution: A breakdown of the most frequent complaint types
citywide vs. district-specific trends.
• Agency Efficiency: Comparison of average response times across different
responding agencies.
• Temporal Trends: Daily or weekly volume of service requests to identify peak
demand periods.
• Bottleneck Analysis: Identifying specific districts where the ratio of open-toclosed requests exceeds the city average.

Apart from required aggregations : 
SLA Compliance per agency 
Hourly heatmap (hour x weekday) 
Channel mix per district  
Complaint-type hotspots by zip
Open-backlog aging distribution
"""

import os
import json
import logging
from datetime import datetime, timezone

import pandas as pd
import pyarrow.parquet as pq

# CONFIGURATION

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SILVER_DIR = os.path.join(PROJECT_ROOT, "data", "silver")
GOLD_DIR = os.path.join(PROJECT_ROOT, "data", "gold")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
GOLD_MANIFEST_PATH = os.path.join(GOLD_DIR, "manifest.json")

# LOGGING SETUP
def setup_logging() -> logging.Logger:
    """Configure logging to both terminal and a timestamped file."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    run_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    log_filepath = os.path.join(LOGS_DIR, f"aggregate_gold_{run_timestamp}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_filepath, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    logger = logging.getLogger(__name__)
    logger.info(f"Logging initialized. Writing log to: {log_filepath}")
    return logger


# LOAD SILVER

def load_silver(logger: logging.Logger) -> pd.DataFrame:
    """
    Read the partitioned Silver dataset into a single DataFrame.
    Reads only .parquet files, skipping manifest.json.
    """
    parquet_files = []
    for entry in sorted(os.listdir(SILVER_DIR)):
        folder = os.path.join(SILVER_DIR, entry)
        if os.path.isdir(folder) and entry.startswith("created_date_partition="):
            for fname in os.listdir(folder):
                if fname.endswith(".parquet"):
                    parquet_files.append(os.path.join(folder, fname))

    if not parquet_files:
        raise FileNotFoundError(
            f"No Parquet files found under {SILVER_DIR}. "
            f"Run src/transform_silver.py first."
        )

    table = pq.ParquetDataset(parquet_files).read()
    df = table.to_pandas()
    logger.info(f"Loaded Silver: {len(df):,} rows, {len(df.columns)} columns")
    return df


# AGGREGATIONS (REQUIRED)

# REQUIREMENT: "District Performance: Average resolution time and total
# volume per Council District."
def build_g1_district_performance(df: pd.DataFrame) -> pd.DataFrame:
    """Avg resolution + volume per council district."""
    result = (
        df.dropna(subset=["council_district"])
        .groupby("council_district", as_index=False)
        .agg(
            total_volume=("unique_key", "count"),
            closed_volume=("is_closed", "sum"),
            avg_resolution_hours=("resolution_time_hours", "mean"),
            median_resolution_hours=("resolution_time_hours", "median"),
        )
        .sort_values("total_volume", ascending=False)
    )
    result["council_district"] = result["council_district"].astype(int)
    result["avg_resolution_hours"] = result["avg_resolution_hours"].round(2)
    result["median_resolution_hours"] = result["median_resolution_hours"].round(2)
    return result


# REQUIREMENT: "Complaint Distribution: A breakdown of the most frequent
# complaint types citywide vs. district-specific trends."
def build_g2_complaint_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """Top complaint types - total volume + per-district top counts."""
    # Citywide totals
    citywide = (
        df.groupby("complaint_type", as_index=False)
        .agg(citywide_volume=("unique_key", "count"))
    )
    # Per-district counts
    by_district = (
        df.dropna(subset=["council_district"])
        .groupby(["complaint_type", "council_district"], as_index=False)
        .agg(district_volume=("unique_key", "count"))
    )
    # Top district per complaint type (which district reports each issue most)
    top_district = (
        by_district.sort_values("district_volume", ascending=False)
        .drop_duplicates("complaint_type")
        .rename(columns={
            "council_district": "top_district",
            "district_volume": "top_district_volume",
        })
    )
    top_district["top_district"] = top_district["top_district"].astype(int)
    result = citywide.merge(top_district, on="complaint_type", how="left").sort_values(
        "citywide_volume", ascending=False
    )
    return result


# REQUIREMENT: "Agency Efficiency: Comparison of average response times
# across different responding agencies."
def build_g3_agency_efficiency(df: pd.DataFrame) -> pd.DataFrame:
    """Avg + median resolution and closure rate per agency."""
    result = (
        df.groupby(["agency", "agency_name"], as_index=False)
        .agg(
            total_volume=("unique_key", "count"),
            closed_volume=("is_closed", "sum"),
            avg_resolution_hours=("resolution_time_hours", "mean"),
            median_resolution_hours=("resolution_time_hours", "median"),
        )
        .sort_values("total_volume", ascending=False)
    )
    result["closure_rate"] = (result["closed_volume"] / result["total_volume"]).round(4)
    result["avg_resolution_hours"] = result["avg_resolution_hours"].round(2)
    result["median_resolution_hours"] = result["median_resolution_hours"].round(2)
    return result


# REQUIREMENT: "Temporal Trends: Daily or weekly volume of service requests
# to identify peak demand periods."
def build_g4_temporal_trends(df: pd.DataFrame) -> pd.DataFrame:
    """Daily volume + closure stats."""
    daily = (
        df.groupby("created_date_partition", as_index=False)
        .agg(
            total_volume=("unique_key", "count"),
            closed_volume=("is_closed", "sum"),
            avg_resolution_hours=("resolution_time_hours", "mean"),
        )
        .sort_values("created_date_partition")
    )
    daily["closure_rate"] = (daily["closed_volume"] / daily["total_volume"]).round(4)
    daily["avg_resolution_hours"] = daily["avg_resolution_hours"].round(2)
    # Adding day-of-week for the dashboard
    daily["created_date_partition"] = pd.to_datetime(daily["created_date_partition"])
    daily["day_of_week"] = daily["created_date_partition"].dt.day_name()
    return daily


# REQUIREMENT: "Bottleneck Analysis: Identifying specific districts where
# the ratio of open-to-closed requests exceeds the city average."
def build_g5_bottleneck_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """Districts with open/closed ratio above the city-wide average."""
    df_with_district = df.dropna(subset=["council_district"])

    # Citywide 
    citywide_open = int((~df_with_district["is_closed"]).sum())
    citywide_closed = int(df_with_district["is_closed"].sum())
    citywide_ratio = citywide_open / citywide_closed if citywide_closed else float("nan")

    # Per district
    per_district = (
        df_with_district.groupby("council_district", as_index=False)
        .agg(
            open_volume=("is_closed", lambda s: int((~s).sum())),
            closed_volume=("is_closed", "sum"),
        )
    )
    per_district["open_to_closed_ratio"] = (
        per_district["open_volume"] / per_district["closed_volume"]
    ).round(4)
    per_district["citywide_ratio"] = round(citywide_ratio, 4)
    per_district["exceeds_citywide"] = (
        per_district["open_to_closed_ratio"] > citywide_ratio
    )
    per_district["council_district"] = per_district["council_district"].astype(int)

    return per_district.sort_values("open_to_closed_ratio", ascending=False)


# ADDITIONAL AGGREGATIONS 

# % of closed cases resolved within the agency's own SLA.
# Compares closed_date against the due_date the agency set.
def build_g6_sla_compliance(df: pd.DataFrame) -> pd.DataFrame:
    """% of closed cases resolved within SLA per agency."""
    closed = df[df["is_closed"]].dropna(subset=["due_date", "closed_date"]).copy()
    closed["within_sla"] = closed["closed_date"] <= closed["due_date"]
    result = (
        closed.groupby(["agency", "agency_name"], as_index=False)
        .agg(
            closed_with_sla=("within_sla", "count"),
            within_sla_count=("within_sla", "sum"),
        )
    )
    result["sla_compliance_pct"] = (
        result["within_sla_count"] / result["closed_with_sla"] * 100
    ).round(2)
    return result.sort_values("sla_compliance_pct", ascending=False)


# Volume by hour-of-day × day-of-week to surface peak demand patterns.
def build_g7_hourly_heatmap(df: pd.DataFrame) -> pd.DataFrame:
    """Volume by hour of day × day of week."""
    result = (
        df.groupby(["created_day_of_week", "created_hour"], as_index=False)
        .agg(volume=("unique_key", "count"))
    )
    return result.sort_values(["created_day_of_week", "created_hour"])


# How citizens in each district report issues (phone/online/mobile).
def build_g8_channel_mix(df: pd.DataFrame) -> pd.DataFrame:
    """Submission-channel mix per council district."""
    result = (
        df.dropna(subset=["council_district"])
        .groupby(["council_district", "open_data_channel_type"], as_index=False)
        .agg(volume=("unique_key", "count"))
    )
    result["council_district"] = result["council_district"].astype(int)
    return result.sort_values(["council_district", "volume"], ascending=[True, False])


# Top zips per complaint type: sub-district hotspot identification.
def build_g9_hotspot_zips(df: pd.DataFrame) -> pd.DataFrame:
    """Top 5 zip codes by volume for each complaint type."""
    by_zip = (
        df.dropna(subset=["incident_zip"])
        .groupby(["complaint_type", "incident_zip"], as_index=False)
        .agg(volume=("unique_key", "count"))
    )
    # Within each complaint type, keeping only the top 5 zips by volume
    by_zip["rank"] = by_zip.groupby("complaint_type")["volume"].rank(
        method="dense", ascending=False
    ).astype(int)
    return by_zip[by_zip["rank"] <= 5].sort_values(
        ["complaint_type", "rank"], ascending=[True, True]
    )


#How long open tickets have been open - gives and actionable backlog view.
def build_g10_open_backlog_aging(df: pd.DataFrame) -> pd.DataFrame:
    """Aging distribution of still-open tickets, bucketed."""
    open_tickets = df[~df["is_closed"]].copy()
    if open_tickets.empty:
        return pd.DataFrame(columns=["age_bucket", "volume"])
    now = pd.Timestamp.now()
    open_tickets["age_days"] = (now - open_tickets["created_date"]).dt.days
    open_tickets["age_bucket"] = pd.cut(
        open_tickets["age_days"],
        bins=[-1, 1, 7, 30, 90, float("inf")],
        labels=["<1d", "1-7d", "7-30d", "30-90d", ">90d"],
    ).astype(str)
    result = (
        open_tickets.groupby("age_bucket", as_index=False)
        .agg(volume=("unique_key", "count"))
    )
    return result

def build_g11_geo_density(df: pd.DataFrame) -> pd.DataFrame:
    """Per-district centroid + volume + avg resolution for the dashboard map."""
    geo = (
        df.dropna(subset=["council_district", "latitude", "longitude"])
        .groupby("council_district", as_index=False)
        .agg(
            total_volume=("unique_key", "count"),
            avg_latitude=("latitude", "mean"),
            avg_longitude=("longitude", "mean"),
            avg_resolution_hours=("resolution_time_hours", "mean"),
            closed_volume=("is_closed", "sum"),
        )
    )
    # Also surface the top complaint type per district (useful tooltip on hover)
    top_complaint = (
        df.dropna(subset=["council_district"])
        .groupby(["council_district", "complaint_type"])
        .size()
        .reset_index(name="ct_volume")
        .sort_values("ct_volume", ascending=False)
        .drop_duplicates("council_district")
        [["council_district", "complaint_type"]]
        .rename(columns={"complaint_type": "top_complaint_type"})
    )
    geo = geo.merge(top_complaint, on="council_district", how="left")
    geo["council_district"] = geo["council_district"].astype(int)
    geo["avg_resolution_hours"] = geo["avg_resolution_hours"].round(2)
    geo["closure_rate"] = (geo["closed_volume"] / geo["total_volume"]).round(4)
    return geo.sort_values("total_volume", ascending=False)


# WRITEING GOLD

def write_gold_tables(df: pd.DataFrame, logger: logging.Logger) -> dict:
    """Run every aggregation and write each as a single Parquet file."""
    os.makedirs(GOLD_DIR, exist_ok=True)

    # Clearing prior gold files
    for f in os.listdir(GOLD_DIR):
        full = os.path.join(GOLD_DIR, f)
        if os.path.isfile(full) and (f.endswith(".parquet") or f == "manifest.json"):
            os.remove(full)

    table_builders = {
        "g1_district_performance": build_g1_district_performance,
        "g2_complaint_distribution": build_g2_complaint_distribution,
        "g3_agency_efficiency": build_g3_agency_efficiency,
        "g4_temporal_trends": build_g4_temporal_trends,
        "g5_bottleneck_analysis": build_g5_bottleneck_analysis,
        "g6_sla_compliance": build_g6_sla_compliance,
        "g7_hourly_heatmap": build_g7_hourly_heatmap,
        "g8_channel_mix_by_district": build_g8_channel_mix,
        "g9_hotspot_zips": build_g9_hotspot_zips,
        "g10_open_backlog_aging": build_g10_open_backlog_aging,
        "g11_geo_density": build_g11_geo_density,
    }

    table_stats = {}
    for name, builder in table_builders.items():
        logger.info(f"Building {name}...")
        result_df = builder(df)
        output_path = os.path.join(GOLD_DIR, f"{name}.parquet")
        result_df.to_parquet(output_path, index=False)
        size_kb = round(os.path.getsize(output_path) / 1024, 2)
        table_stats[name] = {
            "rows": len(result_df),
            "columns": len(result_df.columns),
            "size_kb": size_kb,
        }
        logger.info(
            f"  -> {name}: {len(result_df):,} rows, "
            f"{len(result_df.columns)} columns, {size_kb} KB"
        )

    return table_stats

# MANIFEST


def write_manifest(
    silver_record_count: int,
    table_stats: dict,
    logger: logging.Logger,
) -> None:
    """Persist a Gold manifest summarizing every table produced."""
    manifest = {
        "layer": "gold",
        "schema_version": 1,
        "source_layer": "silver",
        "source_silver_record_count": silver_record_count,
        "table_count": len(table_stats),
        "tables": table_stats,
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    with open(GOLD_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Manifest written to: {GOLD_MANIFEST_PATH}")



def main():
    logger = setup_logging()
    logger.info("=" * 70)
    logger.info("Starting Gold layer aggregation")
    logger.info("=" * 70)

    start = datetime.now(timezone.utc)
    df = load_silver(logger)
    table_stats = write_gold_tables(df, logger)
    write_manifest(len(df), table_stats, logger)
    duration = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info("=" * 70)
    logger.info(f"Gold aggregation complete in {duration:.1f}s")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()