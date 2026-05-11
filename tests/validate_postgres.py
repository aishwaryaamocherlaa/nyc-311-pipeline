import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
url = (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER')}:"
    f"{os.getenv('POSTGRES_PASSWORD')}@{os.getenv('POSTGRES_HOST')}:"
    f"{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
)
engine = create_engine(url)

EXPECTED_TABLES = [
    "g1_district_performance", "g2_complaint_distribution",
    "g3_agency_efficiency", "g4_temporal_trends", "g5_bottleneck_analysis",
    "g6_sla_compliance", "g7_hourly_heatmap", "g8_channel_mix_by_district",
    "g9_hotspot_zips", "g10_open_backlog_aging",
]

with engine.connect() as conn:
    print(f"Connected to: {os.getenv('POSTGRES_DB')}\n")
    all_pass = True
    for table in EXPECTED_TABLES:
        try:
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
            print(f"  [PASS]  {table:<30} {count:>6,} rows")
        except Exception as e:
            print(f"  [FAIL]  {table:<30} {e}")
            all_pass = False

    
    print("\nTop 5 complaint types citywide (live SQL query):")
    result = conn.execute(text(
        'SELECT complaint_type, citywide_volume '
        'FROM g2_complaint_distribution '
        'ORDER BY citywide_volume DESC LIMIT 5'
    )).fetchall()
    for row in result:
        print(f"  {row[0]:<35} {row[1]:>6,}")

    
    print("\nIndexes on g4_temporal_trends:")
    indexes = conn.execute(text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'g4_temporal_trends'"
    )).fetchall()
    for row in indexes:
        print(f"  {row[0]}")

    print(f"\n{'=' * 50}")
    print(f"ALL TABLES QUERYABLE: {all_pass}")
    print(f"{'=' * 50}")