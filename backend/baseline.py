# backend/baseline.py
# Kratt (KratTrade) baseline: the grid-side state at the moment the signal arrives —
# "how much energy you are currently exporting or importing from the grid" (Qilowatt).
# It is taken from the idle readings just before the signal and stays locked for the whole run.
from collections import deque
from datetime import datetime
import pytz
from sqlite_utils import Database

DB_PATH = "data/mffr.db"
tz = pytz.timezone("Europe/Tallinn")

# Idle readings averaged into the snapshot (smooths sensor noise; ~6 reads at 10 s)
BASELINE_WINDOW_S = 60


def dlog(msg: str):
    print(f"[baseline] {datetime.now(tz).isoformat()}  {msg}")


def _ensure_schema():
    db = Database(DB_PATH)
    db["baseline_state"].create({
        "key": str,
        "baseline_w": float,
        "locked_at": str,
        "window_s": float,
    }, pk="key", if_not_exists=True)

_ensure_schema()


class SignalBaseline:
    """Grid power just before a Kratt signal, locked while the run lasts.

    A run is an uninterrupted period with an active signal; direction or power-limit changes
    within it keep the baseline, since a new snapshot would include the battery's own response.
    """

    def __init__(self, store: bool = True):
        self._idle = deque()       # (time, net_kwh, seconds) of recent idle readings
        self.baseline_w = None     # locked value while a run is active
        self._store_enabled = store   # live tracker records the latest lock in baseline_state

    def on_tick(self, now: datetime, reading, active: bool) -> float | None:
        """Feed every tick's meter reading. Returns the locked baseline (W) while active."""
        if not active:
            self.baseline_w = None
            if reading:
                self._idle.append((now, *reading))
        # Keep only readings that ended within the window before now
        while self._idle and (now - self._idle[0][0]).total_seconds() >= BASELINE_WINDOW_S:
            self._idle.popleft()
        if not active:
            return None

        if self.baseline_w is None:
            energy_kwh = sum(r[1] for r in self._idle)
            seconds = sum(r[2] for r in self._idle)
            self.baseline_w = round(energy_kwh * 3_600_000.0 / seconds, 1) if seconds > 0 else 0.0
            if self._store_enabled:
                if seconds > 0:
                    dlog(f"Locked baseline {self.baseline_w} W from {seconds:.0f} s before the signal")
                else:
                    dlog("No idle readings before the signal — baseline 0 W")
                self._store(now, seconds)
            self._idle.clear()
        return self.baseline_w

    def _store(self, now: datetime, seconds: float):
        try:
            db = Database(DB_PATH)
            db.conn.execute("PRAGMA busy_timeout=5000;")
            with db.conn:
                db["baseline_state"].upsert({
                    "key": "latest",
                    "baseline_w": self.baseline_w,
                    "locked_at": now.isoformat(),
                    "window_s": seconds,
                }, pk="key", alter=True)
        except Exception as e:
            dlog(f"Failed to store baseline: {e}")
