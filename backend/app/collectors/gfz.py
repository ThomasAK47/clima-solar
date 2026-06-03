"""
Dst index collector via NOAA's Kyoto WDC mirror.

URL: https://services.swpc.noaa.gov/products/kyoto-dst.json
Response: list of dicts {"time_tag": "...", "dst": -4}
"""

import logging
from datetime import datetime, timezone

import httpx

from app.models.space_weather import DstData

log = logging.getLogger(__name__)

_DST_URL = "https://services.swpc.noaa.gov/products/kyoto-dst.json"
_TIMEOUT = 15.0


def _parse_ts(raw: str) -> datetime:
    ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


async def fetch_dst(client: httpx.AsyncClient) -> DstData:
    log.debug("→ GET %s", _DST_URL)
    resp = await client.get(_DST_URL, timeout=_TIMEOUT, follow_redirects=True)
    log.debug("← HTTP %d | content-type: %s | body: %d bytes",
              resp.status_code, resp.headers.get("content-type", "?"), len(resp.content))
    resp.raise_for_status()

    data = resp.json()
    log.debug("   parsed: %s with %d rows", type(data).__name__,
              len(data) if isinstance(data, list) else "?")

    if not isinstance(data, list) or not data:
        raise ValueError(f"Unexpected Dst response: {type(data).__name__}")

    if isinstance(data[0], dict):
        log.debug("   format: list-of-dicts | keys: %s", list(data[0].keys()))
        valid = [r for r in data if r.get("dst") is not None]
        log.debug("   total rows: %d | valid (non-null dst): %d", len(data), len(valid))
        if not valid:
            raise ValueError("All Dst rows have null values")
        latest = valid[-1]
        log.debug("   latest row (raw): %s", latest)
        timestamp = _parse_ts(latest["time_tag"])
        dst       = float(latest["dst"])
    else:
        header = [str(h).lower() for h in data[0]]
        log.debug("   format: list-of-lists | header: %s", header)
        rows = [r for r in data[1:] if r[header.index("dst") if "dst" in header else 1] is not None]
        if not rows:
            raise ValueError("No valid Dst rows")
        col    = {h: i for i, h in enumerate(header)}
        latest = rows[-1]
        log.debug("   latest row (raw): %s", latest)
        timestamp = _parse_ts(str(latest[col.get("time_tag", 0)]))
        dst       = float(latest[col.get("dst", 1)])

    log.info("✓ Dst=%.1f nT | source_time=%s | url=%s",
             dst, timestamp.isoformat(), _DST_URL)
    return DstData(timestamp=timestamp, dst_nt=dst)
