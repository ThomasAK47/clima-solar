"""
EMBRACE/INPE scintillation map collector.

Data source: https://embracedata.inpe.br/scintillation/maps/
No authentication required — publicly accessible.

Design:
  fetch_embrace_matrices(client) → EmbraceMatrices
      Downloads the latest S4 and sigma_phi grid matrices.
      Called by the background refresh loop (~every 15 min).

  interpolate_embrace(matrices, lat, lon) → EmbraceData | None
      Pure CPU interpolation — no network, called per /status request.

Available products  : S4, sigma_phi
Not available here  : ROTI, VTEC
Processing delay    : ~6–9 h (normal for EMBRACE)
Grid                : lon −140 to −20 (191 cols), lat −60 to +50 (~128 rows)
Missing data        : −1 → NaN
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import numpy as np

from app.models.space_weather import EmbraceData

log = logging.getLogger(__name__)

_BASE    = "https://embracedata.inpe.br/scintillation/maps"
_TIMEOUT = 20.0

_LON_MIN, _LON_MAX = -140.0, -20.0
_LAT_MIN, _LAT_MAX = -60.0, 50.0


@dataclass
class EmbraceMatrices:
    """Raw grid matrices fetched from EMBRACE — location-independent."""
    fetched_at: datetime
    s4_matrix:  Optional[np.ndarray]   # shape (nrows, 191), float32, NaN = no data
    phi_matrix: Optional[np.ndarray]   # same shape
    s4_map_time:  Optional[datetime]   # timestamp of the S4 file used
    phi_map_time: Optional[datetime]   # timestamp of the sigma_phi file used


# ── URL helpers ───────────────────────────────────────────────────────────────

def _dir_url(param: str, year: int, doy: int) -> str:
    return f"{_BASE}/{param}/{year}/{doy:03d}/"


def _file_url(param: str, year: int, doy: int, hhmm: str) -> str:
    date_str = (datetime(year, 1, 1) + timedelta(days=doy - 1)).strftime("%Y%m%d")
    prefix = "S4_MAP" if param == "s4" else "SIGMAPHI_MAP"
    return f"{_BASE}/{param}/{year}/{doy:03d}/{prefix}_{date_str}_{hhmm}.txt"


def _latest_hhmm(html: str, param: str) -> Optional[str]:
    prefix = "S4_MAP" if param == "s4" else "SIGMAPHI_MAP"
    times = re.findall(rf'{prefix}_\d{{8}}_(\d{{4}})\.txt', html)
    return max(times) if times else None


# ── Network helpers ───────────────────────────────────────────────────────────

async def _find_latest_url(
    client: httpx.AsyncClient, param: str
) -> tuple[Optional[str], Optional[datetime]]:
    """Read today's (and yesterday's) dir listing; return URL + datetime of latest file."""
    now = datetime.now(timezone.utc)
    for delta in range(2):
        day  = now - timedelta(days=delta)
        year = day.year
        doy  = day.timetuple().tm_yday
        try:
            r = await client.get(_dir_url(param, year, doy), timeout=_TIMEOUT)
            if r.status_code != 200:
                continue
            hhmm = _latest_hhmm(r.text, param)
            if not hhmm:
                continue
            date_str = (datetime(year, 1, 1) + timedelta(days=doy - 1)).strftime("%Y%m%d")
            map_dt = datetime.strptime(f"{date_str}{hhmm}", "%Y%m%d%H%M").replace(
                tzinfo=timezone.utc
            )
            return _file_url(param, year, doy, hhmm), map_dt
        except Exception as exc:
            log.debug("EMBRACE dir listing failed (%s doy=%d): %s", param, doy, exc)
    return None, None


async def _download_matrix(
    client: httpx.AsyncClient, url: str
) -> Optional[np.ndarray]:
    """Download and parse a semicolon-delimited ASCII matrix file."""
    try:
        r = await client.get(url, timeout=_TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        rows = [
            [float(v) for v in line.split(";")]
            for line in r.text.strip().splitlines()
            if line.strip()
        ]
        if not rows:
            return None
        arr = np.array(rows, dtype=np.float32)
        arr[arr < 0] = np.nan   # −1 → NaN (missing data)
        return arr
    except Exception as exc:
        log.debug("EMBRACE matrix download failed %s: %s", url, exc)
        return None


# ── Public: called by background refresh loop ─────────────────────────────────

async def fetch_embrace_matrices(client: httpx.AsyncClient) -> EmbraceMatrices:
    """
    Fetch the latest S4 and sigma_phi matrices from EMBRACE.
    Designed to be called every ~15 min by the background refresh loop.
    """
    s4_url,  s4_dt  = await _find_latest_url(client, "s4")
    phi_url, phi_dt = await _find_latest_url(client, "sigma_phi")

    s4_matrix  = await _download_matrix(client, s4_url)  if s4_url  else None
    phi_matrix = await _download_matrix(client, phi_url) if phi_url else None

    now = datetime.now(timezone.utc)

    if s4_matrix is not None:
        valid = int(np.sum(~np.isnan(s4_matrix)))
        log.info("EMBRACE S4 matrix loaded: %s (%d valid cells)", s4_dt, valid)
    else:
        log.warning("EMBRACE S4 matrix unavailable")

    if phi_matrix is not None:
        valid = int(np.sum(~np.isnan(phi_matrix)))
        log.info("EMBRACE sigma_phi matrix loaded: %s (%d valid cells)", phi_dt, valid)
    else:
        log.warning("EMBRACE sigma_phi matrix unavailable")

    return EmbraceMatrices(
        fetched_at=now,
        s4_matrix=s4_matrix,
        phi_matrix=phi_matrix,
        s4_map_time=s4_dt,
        phi_map_time=phi_dt,
    )


# ── Public: called per /status request (no network) ───────────────────────────

def interpolate_embrace(
    matrices: EmbraceMatrices, lat: float, lon: float
) -> Optional[EmbraceData]:
    """
    Bilinear interpolation of cached matrices at (lat, lon).
    Pure CPU — no network calls. Returns None if both matrices are absent
    or if the location is outside the South America coverage area.
    """
    if matrices.s4_matrix is None and matrices.phi_matrix is None:
        return None

    s4    = _interp(matrices.s4_matrix,  lat, lon)
    phi60 = _interp(matrices.phi_matrix, lat, lon)

    # Both pixels are NaN/out-of-range → no useful data for this location
    if s4 is None and phi60 is None:
        return None

    data_time = matrices.s4_map_time or matrices.phi_map_time
    return EmbraceData(
        timestamp=data_time or matrices.fetched_at,
        lat=lat,
        lon=lon,
        s4=s4,
        phi60_rad=phi60,
    )


_BRAZIL_LAT = (-34.0,  6.0)
_BRAZIL_LON = (-74.0, -28.0)
_GRID_STEP  = 1.0


def brazil_heatmap_grid(matrices: Optional[EmbraceMatrices]) -> list[dict]:
    """
    1.5° regular grid covering Brazil (-34→+6 lat, -74→-28 lon).
    Each entry: {lat, lon, s4: float|None, phi60: float|None}.
    Works when matrices is None — returns None for iono fields (geo-only score).
    """
    s4_mat  = matrices.s4_matrix  if matrices is not None else None
    phi_mat = matrices.phi_matrix if matrices is not None else None
    points: list[dict] = []
    lat = _BRAZIL_LAT[0]
    while lat <= _BRAZIL_LAT[1] + 1e-9:
        lon = _BRAZIL_LON[0]
        while lon <= _BRAZIL_LON[1] + 1e-9:
            points.append({
                "lat":   round(lat, 2),
                "lon":   round(lon, 2),
                "s4":    _interp(s4_mat,  lat, lon),
                "phi60": _interp(phi_mat, lat, lon),
            })
            lon += _GRID_STEP
        lat += _GRID_STEP
    return points


def _interp(matrix: Optional[np.ndarray], lat: float, lon: float) -> Optional[float]:
    if matrix is None:
        return None
    nrows, ncols = matrix.shape
    col_f = (lon - _LON_MIN) / (_LON_MAX - _LON_MIN) * (ncols - 1)
    row_f = (lat - _LAT_MIN) / (_LAT_MAX - _LAT_MIN) * (nrows - 1)
    if not (0 <= col_f <= ncols - 1 and 0 <= row_f <= nrows - 1):
        return None
    c0 = min(int(col_f), ncols - 2)
    r0 = min(int(row_f), nrows - 2)
    dc = col_f - c0
    dr = row_f - r0
    v = (matrix[r0,   c0  ] * (1 - dc) * (1 - dr)
       + matrix[r0,   c0+1] * dc       * (1 - dr)
       + matrix[r0+1, c0  ] * (1 - dc) * dr
       + matrix[r0+1, c0+1] * dc       * dr)
    return None if np.isnan(v) else float(v)
