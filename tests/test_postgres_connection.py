import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

user = os.getenv("POSTGRES_USER")
password = os.getenv("POSTGRES_PASSWORD")
host = os.getenv("POSTGRES_HOST")
port = os.getenv("POSTGRES_PORT")
db = os.getenv("POSTGRES_DB")

print(f"Connecting to postgresql://{user}:****@{host}:{port}/postgres ...")


engine = create_engine(f"postgresql+psycopg2://{user}:{password}@{host}:{port}/postgres")

with engine.connect() as conn:
    result = conn.execute(text("SELECT version();"))
    version = result.scalar()
    print(f"Connected!")
    print(f"Server: {version}")