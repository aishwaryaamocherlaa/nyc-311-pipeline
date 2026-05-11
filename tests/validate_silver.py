import json
import os
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SILVER_DIR = os.path.join(PROJECT_ROOT, "data", "silver")
MANIFEST_PATH = os.path.join(SILVER_DIR, "manifest.json")

EXPECTED_COLUMNS = {
    
    "unique_key", "created_date", "closed_date", "due_date",
    "resolution_action_updated_date", "agency", "agency_name",
    "complaint_type", "descriptor", "status", "borough",
    "council_district", "incident_zip", "latitude", "longitude",
    "open_data_channel_type",
 
    "created_date_partition", "created_hour", "created_day_of_week",
    "created_is_weekend", "resolution_time_hours", "resolution_time_bucket",
    "is_closed", "is_overdue", "is_data_quality_issue",
}
 
with open(MANIFEST_PATH, encoding="utf-8") as f:
    manifest = json.load(f)
print(f"Manifest claims: {manifest['output_record_count']:,} rows across "
      f"{manifest['partition_count']} partitions")
print(f"Run timestamp:   {manifest['run_timestamp_utc']}")
print()
 
import pyarrow.parquet as pq
 
parquet_files = []
for entry in sorted(os.listdir(SILVER_DIR)):
    folder = os.path.join(SILVER_DIR, entry)
    if os.path.isdir(folder) and entry.startswith("created_date_partition="):
        for fname in os.listdir(folder):
            if fname.endswith(".parquet"):
                parquet_files.append(os.path.join(folder, fname))
 
table = pq.ParquetDataset(parquet_files).read()
df = table.to_pandas()
print(f"Loaded Parquet:  {len(df):,} rows, {len(df.columns)} columns")
print()

 
match_rows = len(df) == manifest["output_record_count"]
print(f"[1/5] Row count matches manifest:        {match_rows}")
 
actual_columns = set(df.columns)
missing = EXPECTED_COLUMNS - actual_columns
extra = actual_columns - EXPECTED_COLUMNS
columns_ok = not missing and not extra
print(f"[2/5] Expected columns all present:      {columns_ok}")
if missing:
    print(f"      Missing: {missing}")
if extra:
    print(f"      Extra:   {extra}")

 
critical_nulls = (
    df["unique_key"].isna().sum()
    + df["created_date"].isna().sum()
)
critical_ok = critical_nulls == 0
print(f"[3/5] No nulls in critical fields:       {critical_ok}")
if not critical_ok:
    print(f"      Found {critical_nulls} null(s) in critical fields")

 
actual_dq = int(df["is_data_quality_issue"].sum())
expected_dq = manifest["rows_flagged_data_quality_issue"]
dq_ok = actual_dq == expected_dq
print(f"[4/5] DQ flag count matches manifest:    {dq_ok} "
      f"(actual={actual_dq}, manifest={expected_dq})")
 
flagged = df[df["is_data_quality_issue"]]
nulled = flagged["resolution_time_hours"].isna().all() if len(flagged) else True
print(f"[5/5] Flagged rows have null resolution: {nulled}")

 
all_pass = match_rows and columns_ok and critical_ok and dq_ok and nulled
print()
print("=" * 50)
print(f"ALL CHECKS PASSED: {all_pass}")
print("=" * 50)