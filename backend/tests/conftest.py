# The modules read DATA_DIR and open the database at import time: point them at a scratch directory first
import os
import sys
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="mfrr-tracker-tests-")
os.environ.setdefault("LOG_DIR", os.environ["DATA_DIR"])
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
