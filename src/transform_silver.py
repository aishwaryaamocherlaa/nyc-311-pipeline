"""
Silver Layer Transformation Script
Reads raw Bronze JSON files, cleans and validates the data, derives
analytical fields, and writes partitioned Parquet to data/silver/.
"""

# IMPORTS


import os
import json
import glob
import logging
from datetime import datetime, timezone

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq



# Paths
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRONZE_DIR = os.path.join(PROJECT_ROOT, "data", "bronze")
SILVER_DIR = os.path.join(PROJECT_ROOT, "data", "silver")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
SILVER_MANIFEST_PATH = os.path.join(SILVER_DIR, "manifest.json")


# REQUIREMENT: "Ensuring schema consistency and handling null values in
# critical fields." Selected to support all required Gold aggregations,
# all required dashboard filters/visuals, AND additional analytical
# measures (SLA compliance, hourly heatmap, hotspot detection,
# channel-mix analysis, backlog aging).
COLUMNS_TO_KEEP = [
    # Identity & timeline (critical)
    "unique_key",
    "created_date",
    "closed_date",
    "due_date",                          # SLA / overdue flagging
    "resolution_action_updated_date",    # stale-ticket detection

    # Who & what
    "agency",
    "agency_name",
    "complaint_type",
    "descriptor",
    "status",

    # Where
    "borough",
    "council_district",
    "incident_zip",                      # sub-district granularity
    "latitude",
    "longitude",

    # How submitted
    "open_data_channel_type",
]

# REQUIREMENT: "Ensuring schema consistency and handling null values in
# critical fields." these fields are structural skeleton; rows with missing
# these values cannot be analyzed and are dropped. (although, it doesnt look like there are any)
CRITICAL_FIELDS = ["unique_key", "created_date"]

# Partitioning
# Best Practices with Parquet files: partition by the date portion of created_date (~40 daily
# folders for 5.5 weeks of data). Daily granularity gives balanced partition
# sizes (~10K rows each)
PARTITION_COLUMN = "created_date_partition"

# LOGGING SETUP
# BEST PRACTICE: Structured logging - same pattern as Bronze. Each Silver
# transformation run produces a timestamped .log file for auditability,
# plus real-time progress visibility in the terminal.

def setup_logging() -> logging.Logger:
    """Configure logging to both terminal and a timestamped file."""
    os.makedirs(LOGS_DIR, exist_ok=True)

    run_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    log_filename = f"transform_silver_{run_timestamp}.log"
    log_filepath = os.path.join(LOGS_DIR, log_filename)

    log_format = "%(asctime)s | %(levelname)-8s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.FileHandler(log_filepath, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True, 
    )

    logger = logging.getLogger(__name__)
    logger.info(f"Logging initialized. Writing log to: {log_filepath}")
    return logger

# LOADING BRONZE DATA
#Parsing and concatenate into a single DataFrame for the cleaning and new aggregated columns.

def load_bronze(logger: logging.Logger) -> pd.DataFrame:
    """
    Load all Bronze JSON page files into a single DataFrame.
    Returns: pd.DataFrame: one row per 311 service request, all columns from
    the original API response preserved.
    """
    pattern = os.path.join(BRONZE_DIR, "bronze_page_*.json")
    page_files = sorted(glob.glob(pattern))

    if not page_files:
        raise FileNotFoundError(
            f"No bronze_page_*.json files found in {BRONZE_DIR}. "
            f"Run src/ingest_bronze.py first."
        )

    logger.info(f"Loading {len(page_files)} Bronze page file(s) from {BRONZE_DIR}")


    dataframes = []
    for page_file in page_files:
        df = pd.read_json(page_file, dtype=False)
        logger.info(
            f"  Loaded {os.path.basename(page_file)}: {len(df):,} rows, "
            f"{len(df.columns)} columns"
        )
        dataframes.append(df)

    combined = pd.concat(dataframes, ignore_index=True)
    logger.info(f"Combined DataFrame: {len(combined):,} rows, {len(combined.columns)} columns")

    return combined

# CLEAN AND TRANSFORM
# Producing analysis-ready DataFrame with only required columns, correct data types, no broken records,standardized values

def clean_and_transform(df: pd.DataFrame, logger: logging.Logger) -> pd.DataFrame:
    
    initial_count = len(df)
    logger.info(f"Starting clean_and_transform on {initial_count:,} rows")

    # SCHEMA SELECTION + TYPE CASTING
    # REQUIREMENT: "Ensuring schema consistency and handling null values in critical fields."
    # Trim to only the columns we need for downstream analysis. Some Bronze
    # columns (highway segment, taxi metadata, etc.) are irrelevant for the
    # required Gold aggregations and would just inflate file size.

    present_columns = [c for c in COLUMNS_TO_KEEP if c in df.columns]
    missing_columns = [c for c in COLUMNS_TO_KEEP if c not in df.columns]
    if missing_columns:
        logger.warning(f"Columns missing from Bronze data: {missing_columns}")
    df = df[present_columns].copy()
    logger.info(f"Selected {len(present_columns)} columns: {present_columns}")

    # REQUIREMENT: "You must implement robust data cleaning and validation logic."
    # if a value can't be parsed, set it to NaT instead of crashing." bad values become nulls, will be caught by null-check step
    datetime_columns = [
        "created_date",
        "closed_date",
        "due_date",
        "resolution_action_updated_date",
    ]
    for col in datetime_columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # Convert lat/long to floats.
    numeric_columns = ["latitude", "longitude"]
    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Cast council_district to numeric to enable proper grouping/sorting. Some rows have empty/missing values → NaN.
    if "council_district" in df.columns:
        df["council_district"] = pd.to_numeric(
            df["council_district"], errors="coerce"
        )

    logger.info("Type casting complete (datetimes, lat/long, council_district)")

    #VALIDATION + STANDARDIZATION
    # REQUIREMENT: "Ensuring schema consistency and handling null values in critical fields."
    # REQUIREMENT: "handling null values in critical fields"
    
    
    before = len(df)
    df = df.dropna(subset=CRITICAL_FIELDS).copy()
    dropped_critical = before - len(df)
    logger.info(
        f"Dropped {dropped_critical:,} rows with null critical fields "
        f"({CRITICAL_FIELDS}). Remaining: {len(df):,}"
    )

    # REQUIREMENT: "Standardizing categorical labels (e.g., complaint types or agency names)."
    # strip whitespace, uppercase where it makes semantic sense, fill nulls with placeholders so any groupby's behave.
    # borough: uppercase + fill nulls with 'UNSPECIFIED'
    if "borough" in df.columns:
        df["borough"] = (
            df["borough"]
            .fillna("UNSPECIFIED")
            .astype(str)
            .str.strip()
            .str.upper()
            .replace({"": "UNSPECIFIED", "UNSPECIFIED": "UNSPECIFIED"})
        )

    if "status" in df.columns:
        df["status"] = (
            df["status"]
            .fillna("Unknown")
            .astype(str)
            .str.strip()
        )

    # complaint_type, agency, agency_name: strip whitespace. not changing case of complaint_type
    for col in ["complaint_type", "descriptor", "agency", "agency_name"]:
        if col in df.columns:
            df[col] = df[col].fillna("Unknown").astype(str).str.strip()

    # open_data_channel_type: uppercase 
    if "open_data_channel_type" in df.columns:
        df["open_data_channel_type"] = (
            df["open_data_channel_type"]
            .fillna("UNKNOWN")
            .astype(str)
            .str.strip()
            .str.upper()
        )

    # incident_zip: string, strip.
    if "incident_zip" in df.columns:
        df["incident_zip"] = df["incident_zip"].fillna("").astype(str).str.strip()
        # Empty string after stripping = treat as null for grouping
        df.loc[df["incident_zip"] == "", "incident_zip"] = None

    logger.info("Categorical standardization complete")

    # REQUIREMENT: "Calculating derived temporal metrics, such as total resolution time for closed cases."
    # NEW DERIVED COLUMNS created here so Gold aggregations don't have to recompute them and the dashboard can filter on them directly.
    # Temporal derivations (from created_date)

    # Partition key: date portion of created_date. MAINLY FOR LATER AT THE END OF PHASE 2 as the partition
    # column for the Parquet (BEST PRACTICE)
    df[PARTITION_COLUMN] = df["created_date"].dt.date

    # Hour of day (0-23): drives peak-demand analysis.
    df["created_hour"] = df["created_date"].dt.hour

    # Day of week
    df["created_day_of_week"] = df["created_date"].dt.day_name()

    # weekend flag
    df["created_is_weekend"] = df["created_day_of_week"].isin(["Saturday", "Sunday"])

    # Resolution time. Open tickets (null closed_date have resolution_time_hours = NaN
    resolution_delta = df["closed_date"] - df["created_date"]
    df["resolution_time_hours"] = resolution_delta.dt.total_seconds() / 3600

    # REQUIREMENT: closed_date earlier than created_date is wrong entry. flag them AND null out the bad metric
    df["is_data_quality_issue"] = (
        df["closed_date"].notna()
        & (df["closed_date"] < df["created_date"])
    )
  
    df.loc[df["is_data_quality_issue"], "resolution_time_hours"] = pd.NA

    # Resolution time bucket (could be used as a filter)

    df["resolution_time_bucket"] = pd.cut(
        df["resolution_time_hours"],
        bins=[-float("inf"), 1, 24, 24 * 7, float("inf")],
        labels=["<1h", "1-24h", "1-7d", ">7d"],
    ).astype("object")
    # Records still open (null resolution_time) get an explicit "unresolved"
    # label so they appear in distribution charts instead of disappearing.
    df.loc[df["resolution_time_hours"].isna(), "resolution_time_bucket"] = "unresolved"

    # Status: is_closed: for bottleneck analysis (open-to-closed ratios) backlog aging.
    df["is_closed"] = df["status"] == "Closed"

    # is_overdue: open ticket whose due_date has passed.
    now = pd.Timestamp.now()
    df["is_overdue"] = (~df["is_closed"]) & (df["due_date"] < now)

    logger.info("Derived columns computed:")
    logger.info(f"  - {PARTITION_COLUMN}, created_hour, created_day_of_week, created_is_weekend")
    logger.info(f"  - resolution_time_hours, resolution_time_bucket")
    logger.info(f"  - is_closed, is_overdue, is_data_quality_issue")


    dq_count = int(df["is_data_quality_issue"].sum())
    closed_count = int(df["is_closed"].sum())
    overdue_count = int(df["is_overdue"].sum())
    final_count = len(df)
    logger.info(
        f"Transformation complete: {final_count:,} rows | "
        f"{closed_count:,} closed | {overdue_count:,} overdue open | "
        f"{dq_count:,} data quality issues"
    )

    return df


# Storage Requirements: The final processed data from this layer must be stored as
#partitioned Parquet files. Choose a partitioning strategy that optimizes
#downstream analytical queries

# STRATEGY: Partitioned Parquet for Analytical Workloads: partitioning by created_date_partition helps with
# speedups on filtered analytical queries.
#Writing the cleaned DataFrame as partitioned Parquet. Partitioned by created_date_partition (date level only, not time), producing
# one folder per calendar day. Each folder contains a single .parquet file with all that day's records.

def write_silver(df: pd.DataFrame, logger: logging.Logger) -> dict:
    
    # Make sure data/silver/ exists. Wipe its prior contents so we don't
    # leave stale partitions from a previous run mixed with the current run.
    if os.path.exists(SILVER_DIR):

        for entry in os.listdir(SILVER_DIR):
            entry_path = os.path.join(SILVER_DIR, entry)
            if os.path.isdir(entry_path) and entry.startswith(f"{PARTITION_COLUMN}="):
                for f in os.listdir(entry_path):
                    os.remove(os.path.join(entry_path, f))
                os.rmdir(entry_path)
            elif entry == "manifest.json":
                os.remove(entry_path)
        logger.info(f"Cleared prior contents of {SILVER_DIR}")

    os.makedirs(SILVER_DIR, exist_ok=True)

    # Convert pandas DataFrame → pyarrow Table.
    logger.info("Converting DataFrame to pyarrow Table...")
    table = pa.Table.from_pandas(df, preserve_index=False)

    logger.info(
        f"Writing partitioned Parquet to {SILVER_DIR} "
        f"(partitioned by '{PARTITION_COLUMN}')..."
    )
# REQUIREMENT Storage Requirements: The final processed data from this layer must be stored as
# partitioned Parquet files. Choose a partitioning strategy that optimizes downstream analytical queries.
# Strategy: daily partitioning by created_date_partition.
# - Cardinality (~40 folders) balanced against row volume (~10K rows/folder)
# - Aligns with the required F3 "Time Horizon" dashboard filter, enabling partition pruning on the most common query pattern

# Writing the table as a partitioned Parquet dataset:
# root_path: Output directory; pyarrow is creating one subfolder per distinct value of the partition column.
# partition_cols: Column(s) being used to split the data into folders.The partition column is being stripped from the file
# contents; its value is living in the folder name (e.g., created_date_partition=2026-04-01/), avoiding redundant storage and enabling
# partition pruning at read time.
# existing_data_behavior: Overwriting any prior files in this dataset (acts as a safety net).
# basename_template: Producing a deterministic filename inside each partition so downstream consumers are referencing files by predictable paths.

    pq.write_to_dataset(
        table,
        root_path=SILVER_DIR,
        partition_cols=[PARTITION_COLUMN],
        existing_data_behavior="overwrite_or_ignore",
        basename_template="part-{i}.parquet",
    )

    # Count of partition folders created.
    partition_folders = [
        d for d in os.listdir(SILVER_DIR)
        if os.path.isdir(os.path.join(SILVER_DIR, d))
        and d.startswith(f"{PARTITION_COLUMN}=")
    ]

    # Sum total bytes on disk for the parquet files (only for summary - manifest).
    total_bytes = 0
    for folder in partition_folders:
        folder_path = os.path.join(SILVER_DIR, folder)
        for fname in os.listdir(folder_path):
            total_bytes += os.path.getsize(os.path.join(folder_path, fname))
    total_mb = round(total_bytes / 1024 / 1024, 2)

    logger.info(
        f"Wrote {len(partition_folders)} partition folder(s), "
        f"{total_mb:,} MB total"
    )

    return {
        "partition_count": len(partition_folders),
        "total_size_mb": total_mb,
        "partition_column": PARTITION_COLUMN,
        "row_count": len(df),
        "column_count": len(df.columns),
    }

# EVALUATION CRITERION: "Pipeline Architecture: Evidence of a clear
# separation of concerns (Bronze, Silver, Gold)."
# The manifest is the contract this layer publishes to downstream Gold:
# what was processed, what was dropped, what was flagged, and the
# resulting partition layout: all auditable without re-running the script.

# MANIFEST WRITER  ( BEST PRACTICE, same pattern as Bronze)

def write_manifest(
    summary: dict,
    bronze_record_count: int,
    transform_stats: dict,
    logger: logging.Logger,
) -> None:
    """Persist a JSON manifest describing the Silver run."""
    manifest = {
        "layer": "silver",
        "schema_version": 1,
        "source_layer": "bronze",
        "source_bronze_record_count": bronze_record_count,
        "output_record_count": summary["row_count"],
        "output_column_count": summary["column_count"],
        "partition_column": summary["partition_column"],
        "partition_count": summary["partition_count"],
        "total_size_mb": summary["total_size_mb"],
        "rows_dropped_null_critical_fields": transform_stats["dropped_critical"],
        "rows_flagged_data_quality_issue": transform_stats["dq_count"],
        "rows_closed": transform_stats["closed_count"],
        "rows_overdue_open": transform_stats["overdue_count"],
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

    with open(SILVER_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    logger.info(f"Manifest written to: {SILVER_MANIFEST_PATH}")


def main():
# Running the full Silver transformation pipeline end-to-end.
    logger = setup_logging()
    logger.info("=" * 70)
    logger.info("Starting Silver layer transformation")
    logger.info("=" * 70)

    start_time = datetime.now(timezone.utc)

    # Phase 1 output to Phase 2 input
    df = load_bronze(logger)
    bronze_record_count = len(df)

    # cleaning + derivation, also feeding data to manifest
    df = clean_and_transform(df, logger)
    transform_stats = {
        "dropped_critical": bronze_record_count - len(df),
        "dq_count": int(df["is_data_quality_issue"].sum()),
        "closed_count": int(df["is_closed"].sum()),
        "overdue_count": int(df["is_overdue"].sum()),
    }

    # Writing partitioned Parquet
    summary = write_silver(df, logger)

    # manifest
    write_manifest(summary, bronze_record_count, transform_stats, logger)

    end_time = datetime.now(timezone.utc)
    duration_seconds = (end_time - start_time).total_seconds()
    logger.info("=" * 70)
    logger.info(f"Silver transformation complete in {duration_seconds:.1f}s")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()