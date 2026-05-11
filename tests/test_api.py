import os
import requests
from dotenv import load_dotenv

load_dotenv()

APP_TOKEN = os.getenv("APP_TOKEN")

url = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
params = {"$limit": 5}
headers = {"X-App-Token": APP_TOKEN}
response = requests.get(url, params=params, headers=headers)

print("Status code:", response.status_code)
print("Number of records returned:", len(response.json()))
print("App token loaded:", APP_TOKEN[:6] + "..." if APP_TOKEN else "NONE")