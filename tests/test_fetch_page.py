import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import requests
from ingest_bronze import setup_logging, fetch_page

logger = setup_logging()
session = requests.Session()

records = fetch_page(session, offset=0, logger=logger)

print(f"Got {len(records)} records. First unique_key: {records[0]['unique_key']}")