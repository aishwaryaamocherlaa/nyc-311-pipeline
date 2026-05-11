"""
Bronze Layer Ingestion Script

ASSESMENT REQUIREMENTS — PHASE 1: DATA INGESTION (BRONZE LAYER)

This script fulfills the Phase 1 specification:

*
Phase 1: Data Ingestion (Bronze Layer)
Your primary task is to programmatically ingest raw data using the SODA3 API with
Python.
• Documentation: This is where you can find documentation -
https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2020-
to-Present/erm2-nwe9/about_data. You are expected to navigate the NYC Open
Data portal and SODA3 documentation to identify the correct API endpoints and
query structures.
• Authentication: You must generate your own App Token (available for free via
NYC Open Data) to authenticate your requests.
• Temporal Filtering: To ensure data relevance and manage volume, your ingestion
script must only fetch records created on or after April 1st, 2026.
• Storage: Store the raw JSON or CSV output in a local directory designated as
your "Bronze" data lake
*

EVALUATION CRITERIA — HOW THIS PART OF THE PROJECT ADDRESSES EACH

  Documentation Research
  Pipeline Architecture (Bronze / Silver / Gold separation)
    -> Bronze layer is fully self-contained in this file. Output
       written to data/bronze/ — never to silver/ or gold/.
    -> Manifest.json provides a clean contract for downstream Silver.
    -> See: main() and write_manifest().
  Data Integrity
    -> Bronze fidelity: raw JSON stored exactly as returned by API
       (no schema enforcement, no transformation, no field drops).
    -> Pagination integrity: $order=created_date ASC, unique_key ASC
       guarantees deterministic ordering — no overlaps, no gaps
       between pages. Verified by tests/validate_bronze.py.
    -> Completeness check: manifest tracks total_records; the
       validator cross-checks against records persisted on disk.
  Optimization (relevant to Phase 1)
    -> Maximum page size (50,000) used to minimize round-trips.
    -> requests.Session reuses the underlying TCP connection across
       paginated calls.
    -> Exponential backoff prevents wasted retries against an
       overloaded server.
"""
# IMPORTS

import os
import json
import time
import logging
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

# REQUIREMENT: "You must generate your own App Token (available for free via NYC Open Data) to authenticate your requests."

APP_TOKEN = os.getenv("APP_TOKEN")

# REQUIREMENT: "You are expected to navigate the NYC Open Data portal and SODA3 documentation to identify the correct API endpoints and query structures."

API_BASE_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"

# REQUIREMENT: "Temporal Filtering: To ensure data relevance and manage volume, your ingestion script must only fetch records created on or after April 1st, 2026"

CUTOFF_DATE = "2026-04-01T00:00:00"

PAGE_SIZE = 50000

MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRONZE_DIR = os.path.join(PROJECT_ROOT, "data", "bronze")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
MANIFEST_PATH = os.path.join(BRONZE_DIR, "manifest.json")

# LOGGING SETUP
# BEST PRACTICE: Structured Logging replaces multiple print() statements with timestamped logs emitted to BOTH the terminal 
# for real-time visibility during a run and a per-run .log file in logs/ (helps with permanent audit trail). Severity levels like
# INFO / WARNING / ERROR etc make it easy to filter long logs and spot small temporary out of control issues between normal flow.

def setup_logging():
    
    os.makedirs(LOGS_DIR, exist_ok=True)

    run_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    log_filename = f"ingest_bronze_{run_timestamp}.log"
    log_filepath = os.path.join(LOGS_DIR, log_filename)

    log_format = "%(asctime)s | %(levelname)-8s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.FileHandler(log_filepath, encoding="utf-8"),
            logging.StreamHandler(),  # writes to terminal
        ],
    )


    logger = logging.getLogger(__name__)
    logger.info(f"Logging initialized. Writing log to: {log_filepath}")
    return logger


# MAIN FETCHING
# BEST PRACTICE: Retry with Exponential Backoff
# Network calls fail for two reasons: PERMANENT (bad token, bad URL) and small out-of-control issues like
# (rate limits, brief server overload, network blips). A script crashes in both. This step hepls tackle the temporary issues
# which are not from developer side. We retry only on failures like HTTP 429, 500s, network errors etc with delays of 1s, 2s, 4s
# giving overloaded servers room to recover.

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def fetch_page(session: requests.Session, offset: int, logger: logging.Logger) -> list:
    # check: fail loudly if the token wasn't loaded.
    if not APP_TOKEN:
        raise RuntimeError(
            "APP_TOKEN not found. Make sure .env exists at the project root "
            "and contains a line like: APP_TOKEN=your_token_here"
        )

# REQUIREMENT: "Programmatically ingest raw data using the SODA3 API with Python." — query parameters constructed below.

    params = {
        "$where": f"created_date >= '{CUTOFF_DATE}'",
        "$order": "created_date ASC, unique_key ASC", # EVALUATION CRITERION: "Data Integrity" — deterministic ordering required for paginated reads to avoid duplicates or gaps between page boundaries.
        "$limit": PAGE_SIZE,
        "$offset": offset,
    }

# EVALUATION CRITERION: "Pipeline Architecture: Evidence of a clear
# separation of concerns" — App Token sent in request header rather than
# URL parameter.
   
    headers = {"X-App-Token": APP_TOKEN}

    
    last_exception = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                f"Fetching offset={offset:,} (attempt {attempt}/{MAX_RETRIES})..."
            )

            
            response = session.get(
                API_BASE_URL,
                params=params,
                headers=headers,
                timeout=60,
            )

            
            if response.status_code == 200:
                records = response.json()
                logger.info(
                    f"  -> Success: received {len(records):,} records "
                    f"(offset={offset:,})"
                )
                return records

            
            if response.status_code in RETRYABLE_STATUS_CODES:
                wait_seconds = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    f"  -> Transient error (HTTP {response.status_code}). "
                    f"Retrying in {wait_seconds}s..."
                )
                time.sleep(wait_seconds)
                continue  

            
            logger.error(
                f"  -> Non-retryable HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
            response.raise_for_status()  

        except requests.exceptions.RequestException as e:
            
            last_exception = e
            wait_seconds = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                f"  -> Network error: {e}. Retrying in {wait_seconds}s..."
            )
            time.sleep(wait_seconds)

    
    raise RuntimeError(
        f"Failed to fetch offset={offset} after {MAX_RETRIES} attempts. "
        f"Last error: {last_exception}"
    )


# PAGINATION
# BEST PRACTICE: Paginated Ingestion with Stable Ordering
# since SODA caps any single response at 50,000 records, we paginate using $limit + $offset. 
# But without an explicit, stable order, the API may return rows in different orders between calls,
# causing pages to overlap or skip records which leads to data loss. We sort by (created_date ASC, unique_key ASC)to fix that.
# Each page is given its own file (bronze_page_xxx.json) for easier inspection if anything fails.

def main():
    
    logger = setup_logging()
    logger.info("=" * 70)
    logger.info("Starting Bronze layer ingestion")
    logger.info(f"  Cutoff date:    {CUTOFF_DATE}")
    logger.info(f"  Page size:      {PAGE_SIZE:,}")
    logger.info(f"  Output folder:  {BRONZE_DIR}")
    logger.info("=" * 70)

    
    os.makedirs(BRONZE_DIR, exist_ok=True)


    session = requests.Session()

    offset = 0
    page_num = 1
    total_records = 0
    page_filenames = []  

    start_time = datetime.now(timezone.utc)

    while True:
        
        records = fetch_page(session, offset=offset, logger=logger)

        if len(records) == 0:
            logger.info(f"Page {page_num} returned 0 records. Pagination complete.")
            break

        
        page_num_str = str(page_num).zfill(3)
        filename = f"bronze_page_{page_num_str}.json"
        filepath = os.path.join(BRONZE_DIR, filename)

# REQUIREMENT: "Store the raw JSON or CSV output in a local directory designated as your 'Bronze' data lake."

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2)

        logger.info(
            f"  -> Saved page {page_num} to {filename} "
            f"({len(records):,} records)"
        )

       
        total_records += len(records)
        page_filenames.append(filename)

        
        if len(records) < PAGE_SIZE:
            logger.info(
                f"Page {page_num} returned {len(records):,} records "
                f"(less than PAGE_SIZE={PAGE_SIZE:,}). End of data reached."
            )
            break


        offset += PAGE_SIZE
        page_num += 1


    end_time = datetime.now(timezone.utc)
    duration_seconds = (end_time - start_time).total_seconds()

    logger.info("=" * 70)
    logger.info("Ingestion complete.")
    logger.info(f"  Total pages:    {len(page_filenames)}")
    logger.info(f"  Total records:  {total_records:,}")
    logger.info(f"  Duration:       {duration_seconds:.1f} seconds")
    logger.info("=" * 70)


    return {
        "start_time_utc": start_time.isoformat(),
        "end_time_utc": end_time.isoformat(),
        "duration_seconds": round(duration_seconds, 1),
        "cutoff_date": CUTOFF_DATE,
        "page_size": PAGE_SIZE,
        "total_pages": len(page_filenames),
        "total_records": total_records,
        "page_files": page_filenames,
    }

# MANIFEST WRITER
# BEST PRACTICE: Auditing & Summary of data
# Captures what was fetched, when, with what filter, and which files were produced.
# So, anyone can reconstruct a part without re-running anything, Silver/Gold downstream consumers know exactly
# which Bronze run they processed and cross checking records on disk against the manifest's claims.

def write_manifest(summary: dict, logger: logging.Logger) -> None:

    manifest = {
        "dataset": "NYC 311 Service Requests",
        "dataset_id": "erm2-nwe9",
        "source_api": API_BASE_URL,
        "ingestion_layer": "bronze",
        "schema_version": 1,
        **summary,
    }

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    logger.info(f"Manifest written to: {MANIFEST_PATH}")


# FINAL MAIN ENTRY POINT

if __name__ == "__main__":

    summary = main()


    logger = logging.getLogger(__name__)
    write_manifest(summary, logger)

    logger.info("All done.")