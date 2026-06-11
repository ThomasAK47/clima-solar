"""
SQLite history store for the /history endpoint.

Design decisions:
  - DB path: /data/historia.db (Railway Volume) — configurable via DB_PATH env var
  - Retention: 48 h (TTL_HRS), enforced on every write
  - Deduplication: skips write if a record for the same rounded location
    already exists within the last 10 minutes (prevents flood from multiple tabs)
  - Location precision: lat/lon rounded to 1 decimal (~11 km) — groups
    nearby users into the same bucket; precise enough for ionospheric context
  - Thread safety: all calls wrapped in asyncio.to_thread() in main.py
    so synchronous SQLite never blocks the async event loop
"""

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DB_PATH  = Path(os.getenv("DB_PATH", "/data/historia.db"))
TTL_HRS  = 48
_GAP_MIN = 10   # minimum minutes between records for same location


# ── Connection helper ─────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


# ── Public API ────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables and indexes if they don't exist. Called once at startup."""
    try:
        with _conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS history (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        TEXT    NOT NULL,
                    lat       REAL    NOT NULL,
                    lon       REAL    NOT NULL,
                    score     REAL,
                    kp        REAL,
                    dst_nt    REAL,
                    f107_sfu  REAL,
                    s4        REAL,
                    phi60_rad REAL
                );
                CREATE INDEX IF NOT EXISTS idx_ts
                    ON history(ts);
                CREATE INDEX IF NOT EXISTS idx_loc_ts
                    ON history(lat, lon, ts);
            """)
            # Migration: add columns introduced after the table was created
            existing = {row[1] for row in c.execute("PRAGMA table_info(history)")}
            for col in ("roti", "vtec"):
                if col not in existing:
                    c.execute(f"ALTER TABLE history ADD COLUMN {col} REAL")
                    log.info("DB migration: added column history.%s", col)
        log.info("DB ready: %s", DB_PATH)
    except Exception as exc:
        log.warning("DB init failed (%s) — history will not be persisted", exc)


def save_snapshot(
    lat: float, lon: float,
    score: float,
    kp: float | None,
    dst_nt: float | None,
    f107_sfu: float | None,
    s4: float | None,
    phi60_rad: float | None,
    roti: float | None = None,
    vtec: float | None = None,
) -> None:
    """
    Insert one record for this location.
    Silently skips if a record already exists within the last _GAP_MIN minutes
    (deduplication guard against frequent refreshes / multiple tabs).
    Also purges records older than TTL_HRS on every write.
    """
    rlat, rlon = round(lat, 1), round(lon, 1)
    now        = datetime.now(timezone.utc)
    gap_cutoff = (now - timedelta(minutes=_GAP_MIN)).isoformat()
    ttl_cutoff = (now - timedelta(hours=TTL_HRS)).isoformat()

    try:
        with _conn() as c:
            # Dedup check
            exists = c.execute(
                "SELECT 1 FROM history WHERE lat=? AND lon=? AND ts>? LIMIT 1",
                (rlat, rlon, gap_cutoff),
            ).fetchone()
            if exists:
                return

            c.execute(
                "INSERT INTO history(ts,lat,lon,score,kp,dst_nt,f107_sfu,s4,phi60_rad,roti,vtec)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (now.isoformat(), rlat, rlon,
                 score, kp, dst_nt, f107_sfu, s4, phi60_rad, roti, vtec),
            )
            # Purge old records while connection is open
            c.execute("DELETE FROM history WHERE ts<?", (ttl_cutoff,))
    except Exception as exc:
        log.warning("DB write failed: %s", exc)


def get_history(lat: float, lon: float) -> list[dict]:
    """
    Return all records for this rounded location within the TTL window,
    ordered oldest-first (for chart rendering).
    """
    rlat, rlon = round(lat, 1), round(lon, 1)
    cutoff     = (datetime.now(timezone.utc) - timedelta(hours=TTL_HRS)).isoformat()

    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT ts,score,kp,dst_nt,f107_sfu,s4,phi60_rad,roti,vtec"
                " FROM history"
                " WHERE lat=? AND lon=? AND ts>?"
                " ORDER BY ts ASC",
                (rlat, rlon, cutoff),
            ).fetchall()
        return [
            {
                "ts":    r["ts"],
                "score": r["score"],
                "kp":    r["kp"],
                "dst":   r["dst_nt"],
                "f107":  r["f107_sfu"],
                "s4":    r["s4"],
                "phi60": r["phi60_rad"],
                "roti":  r["roti"],
                "vtec":  r["vtec"],
            }
            for r in rows
        ]
    except Exception as exc:
        log.warning("DB read failed: %s", exc)
        return []
