"""sanity check on the Bronze layer output."""
import json
import os
from glob import glob

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRONZE_DIR = os.path.join(PROJECT_ROOT, "data", "bronze")

with open(os.path.join(BRONZE_DIR, "manifest.json"), encoding="utf-8") as f:
    manifest = json.load(f)

print(f"Manifest says: {manifest['total_records']:,} records across {manifest['total_pages']} pages")
print(f"Cutoff date: {manifest['cutoff_date']}")
print()

page_files = sorted(glob(os.path.join(BRONZE_DIR, "bronze_page_*.json")))
actual_total = 0

for pf in page_files:
    with open(pf, encoding="utf-8") as f:
        records = json.load(f)
    actual_total += len(records)
    first_date = records[0]["created_date"]
    last_date = records[-1]["created_date"]
    print(f"{os.path.basename(pf)}: {len(records):>6,} records  |  {first_date}  →  {last_date}")

print()
print(f"Sum of records on disk: {actual_total:,}")
print(f"Manifest claims:        {manifest['total_records']:,}")
print(f"Match: {actual_total == manifest['total_records']}")