"""
NOAA Space Weather Prediction Center collectors.

Endpoints (public, no auth):
  Kp    — https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json
  F10.7 — https://services.swpc.noaa.gov/products/summary/10cm-flux.json
"""

import logging
from datetime import datetime, timezone

import httpx

from app.models.space_weather import F107Data, KpData

log = logging.getLogger(__name__)

_KP_URL   = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
_F107_URL = "https://services.swpc.noaa.gov/products/summary/10cm-flux.json"
_TIMEOUT  = 15.0


def _parse_ts(raw: str) -> datetime:
    ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


async def fetch_kp(client: httpx.AsyncClient) -> KpData:
    log.debug("→ GET %s", _KP_URL)
    resp = await client.get(_KP_URL, timeout=_TIMEOUT, follow_redirects=True)
    log.debug("← HTTP %d | content-type: %s | body: %d bytes",
              resp.status_code, resp.headers.get("content-type", "?"), len(resp.content))
    resp.raise_for_status()

    data = resp.json()
    log.debug("   parsed: %s with %d rows", type(data).__name__, len(data) if isinstance(data, list) else "?")

    if not isinstance(data, list) or not data:
        raise ValueError(f"Unexpected Kp response type: {type(data).__name__}")

    if isinstance(data[0], dict):
        log.debug("   format: list-of-dicts | keys: %s", list(data[0].keys()))
        valid = [r for r in data if r.get("Kp") is not None]
        if not valid:
            raise ValueError("No valid Kp rows in response")
        latest = valid[-1]
        log.debug("   latest row (raw): %s", latest)
        timestamp   = _parse_ts(latest["time_tag"])
        kp_fraction = float(latest["Kp"])
        kp          = float(round(kp_fraction))
    else:
        header = [str(h).lower() for h in data[0]]
        log.debug("   format: list-of-lists | header: %s", header)
        rows = data[1:]
        if not rows:
            raise ValueError("No Kp data rows")
        col    = {h: i for i, h in enumerate(header)}
        latest = rows[-1]
        log.debug("   latest row (raw): %s", latest)
        timestamp   = _parse_ts(str(latest[col.get("time_tag", 0)]))
        kp_fraction = float(latest[col.get("kp_fraction", col.get("kp", 1))])
        kp          = float(round(kp_fraction))

    log.info("✓ Kp=%.2f (fraction=%.2f) | source_time=%s | url=%s",
             kp, kp_fraction, timestamp.isoformat(), _KP_URL)
    return KpData(timestamp=timestamp, kp=kp, kp_fraction=kp_fraction)


async def fetch_f107(client: httpx.AsyncClient) -> F107Data:
    log.debug("→ GET %s", _F107_URL)
    resp = await client.get(_F107_URL, timeout=_TIMEOUT, follow_redirects=True)
    log.debug("← HTTP %d | content-type: %s | body: %d bytes",
              resp.status_code, resp.headers.get("content-type", "?"), len(resp.content))
    resp.raise_for_status()

    data = resp.json()
    log.debug("   parsed: %s | value: %s", type(data).__name__, data)

    if isinstance(data, list):
        if not data:
            raise ValueError("Empty F10.7 response")
        latest = data[-1]
        if isinstance(latest, dict):
            log.debug("   format: list[dict] | latest: %s", latest)
            flux      = float(latest["flux"])
            timestamp = _parse_ts(latest["time_tag"]) if "time_tag" in latest else datetime.now(timezone.utc)
        else:
            header    = [str(h).lower() for h in data[0]]
            rows      = data[1:]
            if not rows:
                raise ValueError("No F10.7 data rows")
            flux_idx  = next((i for i, h in enumerate(header) if "flux" in h), 1)
            flux      = float(rows[-1][flux_idx])
            timestamp = datetime.now(timezone.utc)
    elif isinstance(data, dict):
        log.debug("   format: dict | keys: %s", list(data.keys()))
        flux      = float(data.get("Flux") or data.get("flux"))
        timestamp = datetime.now(timezone.utc)
    else:
        raise ValueError(f"Unrecognised F10.7 response type: {type(data).__name__}")

    log.info("✓ F10.7=%.1f sfu | source_time=%s | url=%s",
             flux, timestamp.isoformat(), _F107_URL)
    return F107Data(timestamp=timestamp, f107_sfu=flux)
