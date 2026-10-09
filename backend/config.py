# config.py — where the tracker keeps its files (/data in the Home Assistant add-on)
import os

DATA_DIR = os.getenv("DATA_DIR", "data")
LOG_DIR = os.getenv("LOG_DIR", "logs")
DB_PATH = os.path.join(DATA_DIR, "mffr.db")

# Publish summary sensors and Energy dashboard statistics to Home Assistant
PUBLISH_SENSORS = os.getenv("PUBLISH_SENSORS", "false").strip().lower() in ("1", "true", "yes", "on")

os.makedirs(DATA_DIR, exist_ok=True)
