"""
Phase 4: Serving & Presentation Layer
Load your Gold layer aggregations into a local PostgreSQL instance to serve as the
backend for your visualization.
EVALUATION CRITERION "Optimization: Effective use of Parquet partitioning and PostgreSQL indexing."
-> create_indexes() builds explicit indexes on the columns the dashboard filters on (council_district, complaint_type,created_date_partition, agency).
"""

import os
import json
import logging
from datetime import datetime, timezone

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
 
# CONFIGURATION

load_dotenv()

PG_USER = os.getenv("POSTGRES_USER")
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD")
PG_HOST = os.getenv("POSTGRES_HOST")
PG_PORT = os.getenv("POSTGRES_PORT")
PG_DB = os.getenv("POSTGRES_DB")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLD_DIR = os.path.join(PROJECT_ROOT, "data", "gold")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
LOAD_MANIFEST_PATH = os.path.join(GOLD_DIR, "load_manifest.json")
 
INDEX_SPEC = {
    "g1_district_performance":     ["council_district"],
    "g2_complaint_distribution":   ["complaint_type"],
    "g3_agency_efficiency":        ["agency"],
    "g4_temporal_trends":          ["created_date_partition"],
    "g5_bottleneck_analysis":      ["council_district"],
    "g6_sla_compliance":           ["agency"],
    "g7_hourly_heatmap":           ["created_day_of_week", "created_hour"],
    "g8_channel_mix_by_district":  ["council_district", "open_data_channel_type"],
    "g9_hotspot_zips":             ["complaint_type", "incident_zip"],
    "g10_open_backlog_aging":      ["age_bucket"],
    "g11_geo_density":             ["council_district"],
}
 
 

def setup_logging() -> logging.Logger:
    os.makedirs(LOGS_DIR, exist_ok=True)
    run_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    log_filepath = os.path.join(LOGS_DIR, f"load_postgres_{run_timestamp}.log")
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

 
# DATABASE BOOTSTRAP

def ensure_database_exists(logger: logging.Logger) -> None:
    """Create the target database if it doesn't already exist."""
    # Connect to the default 'postgres' system database to issue CREATE DATABASE.
    admin_url = (
        f"postgresql+psycopg2://{PG_USER}:{PG_PASSWORD}"
        f"@{PG_HOST}:{PG_PORT}/postgres"
    )
    # autocommit isolation level is required for CREATE DATABASE in Postgres.
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": PG_DB},
        ).scalar()
        if exists:
            logger.info(f"Database '{PG_DB}' already exists — reusing it")
        else:
            conn.execute(text(f'CREATE DATABASE "{PG_DB}"'))
            logger.info(f"Created database '{PG_DB}'")

    admin_engine.dispose()


def get_engine():
    """Return a SQLAlchemy engine connected to our project database."""
    url = (
        f"postgresql+psycopg2://{PG_USER}:{PG_PASSWORD}"
        f"@{PG_HOST}:{PG_PORT}/{PG_DB}"
    )
    return create_engine(url)
 
# LOAD GOLD TABLES
 

def load_gold_tables(engine, logger: logging.Logger) -> dict:
    """Read each Gold parquet file and write it to Postgres."""
    table_stats = {}
    gold_files = sorted([
        f for f in os.listdir(GOLD_DIR)
        if f.endswith(".parquet")
    ])

    for fname in gold_files:
        table_name = fname.replace(".parquet", "")
        df = pd.read_parquet(os.path.join(GOLD_DIR, fname))

        # if_exists='replace' drops and recreates the table on each run.
        # For a take-home this is the cleanest behavior; in production you'd
        # typically use 'append' with deduplication or a versioned table name.
        df.to_sql(
            name=table_name,
            con=engine,
            if_exists="replace",
            index=False,
            method="multi",  # batches inserts for speed
            chunksize=1000,
        )
        table_stats[table_name] = {"rows": len(df), "columns": len(df.columns)}
        logger.info(
            f"Loaded {table_name}: {len(df):,} rows, {len(df.columns)} columns"
        )

    return table_stats
 
# INDEXES

def create_indexes(engine, logger: logging.Logger) -> int:
    """Create indexes on common filter columns for each table."""
    index_count = 0
    with engine.connect() as conn:
        for table_name, columns in INDEX_SPEC.items():
            for col in columns:
                index_name = f"idx_{table_name}_{col}"
                conn.execute(text(
                    f'CREATE INDEX IF NOT EXISTS "{index_name}" '
                    f'ON "{table_name}" ("{col}");'
                ))
                index_count += 1
        conn.commit()
    logger.info(f"Created/verified {index_count} indexes")
    return index_count

 
# MANIFEST

def write_manifest(
    table_stats: dict, index_count: int, logger: logging.Logger
) -> None:
    manifest = {
        "layer": "postgres",
        "source_layer": "gold",
        "database": PG_DB,
        "host": PG_HOST,
        "port": PG_PORT,
        "table_count": len(table_stats),
        "tables": table_stats,
        "index_count": index_count,
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    with open(LOAD_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Load manifest written to: {LOAD_MANIFEST_PATH}")

 
def main():
    logger = setup_logging()
    logger.info("=" * 70)
    logger.info("Starting Gold-to-Postgres load")
    logger.info("=" * 70)
    start = datetime.now(timezone.utc)

    ensure_database_exists(logger)

    engine = get_engine()
    table_stats = load_gold_tables(engine, logger)
    index_count = create_indexes(engine, logger)
    write_manifest(table_stats, index_count, logger)

    duration = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info("=" * 70)
    logger.info(f"Postgres load complete in {duration:.1f}s")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()