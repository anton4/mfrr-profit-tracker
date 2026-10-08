# config.py — where the tracker keeps its files (/data in the Home Assistant add-on)
import os

DATA_DIR = os.getenv("DATA_DIR", "data")
LOG_DIR = os.getenv("LOG_DIR", "logs")
DB_PATH = os.path.join(DATA_DIR, "mffr.db")

os.makedirs(DATA_DIR, exist_ok=True)
