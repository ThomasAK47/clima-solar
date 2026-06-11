"""
EMBRACE/INPE VTEC (AMAP) and ROTI (GTEX) collector.

Data sources (public, no authentication):
  VTEC : https://embracedata.inpe.br/amap/{year}/{doy}/AMAP{doy}T{hhmm}.{yy}I
         TEC maps every 10 min, same ASCII matrix format as the S4 maps
         (';' delimited, -1 = NaN). Grid 160 rows x 120 cols, lat -60..+20,
         lon -90..-30, 0.5 deg, values in TECU. Row 0 = lat -60 (ascending).
  ROTI : https://embracedata.inpe.br/gtex/{year}/{doy}/{stat}{doy}0.{yy}_TEC
         Per-station absolute slant TEC every 30 s per satellite. ROTI is
         computed here as the std-dev of the TEC rate (TECU/min) over 5-min
         windows, binned to 10 min (max across satellites, elevation > 30).

Both products are uploaded daily (~08:00 UTC) with a 1.5-2 day lag, so we
use *time-of-day matching*: the value returned for "now" is the one observed
at the same UT time on the latest available day. The ionosphere is strongly
diurnal, which makes this a reasonable nowcast proxy; the real map timestamp
is always reported so the frontend can show the data age.
"""

import asyncio
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import numpy as np

log = logging.getLogger(__name__)

_AMAP_BASE = "https://embracedata.inpe.br/amap"
_GTEX_BASE = "https://embracedata.inpe.br/gtex"
_TIMEOUT   = 30.0
_MAX_LOOKBACK_DAYS = 4   # how many days back we search for the latest upload

# AMAP grid (verified 2026-06-11: daytime TEC peak at row 139 -> lat +9.5)
_VTEC_LAT_MIN, _VTEC_LAT_MAX = -60.0, 20.0
_VTEC_LON_MIN, _VTEC_LON_MAX = -90.0, -30.0

# ROTI computation parameters
_ROTI_STATIONS = [
    "boav", "saga", "naus", "bele", "impz", "rnna",
    "pepe", "braz", "goja", "onrj", "ufpr", "poal",
]
_ROTI_MAX_ELEV_ZENITH = 60.0    # ZN < 60 deg -> elevation > 30 deg
_ROTI_WINDOW_S        = 300     # 5-min ROTI window (standard)
_ROTI_BIN_S           = 600     # report one value per 10-min bin
_ROTI_MIN_SAMPLES     = 6       # min rate samples in a window to accept it
_ROTI_MAX_RADIUS_KM   = 1200.0  # max distance to nearest station


@dataclass
class VtecMatrix:
    fetched_at: datetime
    matrix:     Optional[np.ndarray]   # (160, 120) float32, NaN = no data
    map_time:   Optional[datetime]     # real observation time of the map used


@dataclass
class RotiStation:
    code:   str
    lat:    float
    lon:    float
    series: np.ndarray                 # (144,) ROTI per 10-min bin, NaN = no data


@dataclass
class RotiData:
    fetched_at: datetime
    day:        Optional[datetime]     # UT day the series refers to
    stations:   list[RotiStation] = field(default_factory=list)


# ── VTEC (AMAP) ───────────────────────────────────────────────────────────────

def _amap_dir(year: int, doy: int) -> str:
    return f"{_AMAP_BASE}/{year}/{doy:03d}/"


async def _amap_listing(
    client: httpx.AsyncClient, year: int, doy: int
) -> list[str]:
    """Return sorted list of HHMM strings available for that day."""
    try:
        r = await client.get(_amap_dir(year, doy), timeout=_TIMEOUT)
        if r.status_code != 200:
            return []
        return sorted(re.findall(r'AMAP\d{3}T(\d{4})\.\d{2}I', r.text))
    except Exception as exc:
        log.debug("AMAP listing failed (%d/%03d): %s", year, doy, exc)
        return []


async def fetch_vtec_matrix(client: httpx.AsyncClient) -> VtecMatrix:
    """
    Download the AMAP TEC map from the latest available day whose time-of-day
    is closest to the current UT time. Called by the background refresh loop.
    """
    now = datetime.now(timezone.utc)
    target_hhmm = now.strftime("%H%M")

    for delta in range(_MAX_LOOKBACK_DAYS):
        day  = now - timedelta(days=delta)
        year = day.year
        doy  = day.timetuple().tm_yday
        times = await _amap_listing(client, year, doy)
        if not times:
            continue

        # File with time-of-day closest to "now"
        hhmm = min(times, key=lambda t: abs(int(t[:2]) * 60 + int(t[2:])
                                            - (now.hour * 60 + now.minute)))
        url = f"{_amap_dir(year, doy)}AMAP{doy:03d}T{hhmm}.{year % 100:02d}I"
        matrix = await _download_matrix(client, url)
        if matrix is None:
            continue

        date0 = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)
        map_time = date0.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]))
        valid = int(np.sum(~np.isnan(matrix)))
        log.info("EMBRACE VTEC map loaded: %s (%d valid cells, target %sz)",
                 map_time, valid, target_hhmm)
        return VtecMatrix(fetched_at=now, matrix=matrix, map_time=map_time)

    log.warning("EMBRACE VTEC map unavailable (no AMAP upload in %d days)",
                _MAX_LOOKBACK_DAYS)
    return VtecMatrix(fetched_at=now, matrix=None, map_time=None)


async def _download_matrix(
    client: httpx.AsyncClient, url: str
) -> Optional[np.ndarray]:
    """Download and parse a semicolon-delimited ASCII matrix file."""
    try:
        r = await client.get(url, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        rows = [
            [float(v) for v in line.split(";") if v.strip()]
            for line in r.text.strip().splitlines()
            if line.strip()
        ]
        if not rows:
            return None
        arr = np.array(rows, dtype=np.float32)
        arr[arr < 0] = np.nan
        return arr
    except Exception as exc:
        log.debug("AMAP matrix download failed %s: %s", url, exc)
        return None


def interpolate_vtec(vtec: Optional[VtecMatrix], lat: float, lon: float) -> Optional[float]:
    """Bilinear interpolation of the cached VTEC matrix at (lat, lon). Pure CPU."""
    if vtec is None or vtec.matrix is None:
        return None
    matrix = vtec.matrix
    nrows, ncols = matrix.shape
    col_f = (lon - _VTEC_LON_MIN) / (_VTEC_LON_MAX - _VTEC_LON_MIN) * (ncols - 1)
    row_f = (lat - _VTEC_LAT_MIN) / (_VTEC_LAT_MAX - _VTEC_LAT_MIN) * (nrows - 1)
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


# ── ROTI (GTEX) ───────────────────────────────────────────────────────────────

def _gtex_dir(year: int, doy: int) -> str:
    return f"{_GTEX_BASE}/{year}/{doy:03d}/"


async def fetch_roti_stations(client: httpx.AsyncClient) -> RotiData:
    """
    Download GTEX files for the selected stations on the latest available day
    and compute per-station ROTI series (one value per 10-min bin).
    Heavy (~12 x 600 KB + parsing) — call at most once per day per doy.
    """
    now = datetime.now(timezone.utc)

    for delta in range(_MAX_LOOKBACK_DAYS):
        day  = now - timedelta(days=delta)
        year = day.year
        doy  = day.timetuple().tm_yday
        try:
            r = await client.get(_gtex_dir(year, doy), timeout=_TIMEOUT)
            if r.status_code != 200:
                continue
            available = set(re.findall(r'href="([a-z0-9]{4})\d{3}0\.\d{2}_TEC"', r.text))
        except Exception as exc:
            log.debug("GTEX listing failed (%d/%03d): %s", year, doy, exc)
            continue
        codes = [c for c in _ROTI_STATIONS if c in available]
        if not codes:
            continue

        stations: list[RotiStation] = []
        for code in codes:
            url = f"{_gtex_dir(year, doy)}{code}{doy:03d}0.{year % 100:02d}_TEC"
            try:
                r = await client.get(url, timeout=_TIMEOUT)
                if r.status_code != 200:
                    continue
                st = await asyncio.to_thread(_parse_gtex_roti, code, r.text)
                if st is not None:
                    stations.append(st)
            except Exception as exc:
                log.debug("GTEX download failed %s: %s", code, exc)

        if stations:
            day0 = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)
            log.info("EMBRACE ROTI computed: %d stations for %s (%s)",
                     len(stations), day0.date(),
                     ", ".join(s.code for s in stations))
            return RotiData(fetched_at=now, day=day0, stations=stations)

    log.warning("EMBRACE ROTI unavailable (no GTEX upload in %d days)",
                _MAX_LOOKBACK_DAYS)
    return RotiData(fetched_at=now, day=None)


def _parse_gtex_roti(code: str, text: str) -> Optional[RotiStation]:
    """
    Parse a GTEX file and compute the ROTI series.

    File layout (after the RINEX-like header):
      epoch line :  YY MM DD HH MM SS.SSS  flag  N + concatenated sat IDs
      then N data lines: A1(TECU)  flag  obs  ZN(deg)  AZ(deg)
    """
    lines = text.splitlines()

    # ── Header: station position + end-of-header index ──
    lat = lon = None
    i = 0
    for i, line in enumerate(lines):
        if "POSITION LAT LON ALT" in line:
            parts = line.split()
            try:
                lat, lon = float(parts[0]), float(parts[1])
            except (ValueError, IndexError):
                pass
        if "END OF HEADER" in line:
            break
    else:
        return None
    if lat is None or lon is None:
        return None

    # ── Body: per-satellite TEC time series (elevation-filtered) ──
    # sat_tec[sat] = list of (seconds_of_day, tec)
    # Satellite IDs are RINEX-style 3-char fields with space padding
    # (e.g. "G 2" = G02), and the list may wrap onto continuation lines.
    sat_tec: dict[str, list[tuple[int, float]]] = {}
    epoch_re = re.compile(
        r'^\s*\d{2}\s+\d{1,2}\s+\d{1,2}\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})'
        r'\.\d+\s+\d+\s+(\d+)((?:[A-Z][ \d]\d)*)\s*$'
    )
    sat_cont_re = re.compile(r'^\s*((?:[A-Z][ \d]\d)+)\s*$')
    j = i + 1
    n = len(lines)
    while j < n:
        m = epoch_re.match(lines[j])
        if not m:
            j += 1
            continue
        hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3))
        nsat = int(m.group(4))
        sats = [s.replace(" ", "0") for s in re.findall(r'[A-Z][ \d]\d', m.group(5))]
        while len(sats) < nsat and j + 1 < n:
            mc = sat_cont_re.match(lines[j + 1])
            if not mc:
                break
            sats += [s.replace(" ", "0")
                     for s in re.findall(r'[A-Z][ \d]\d', mc.group(1))]
            j += 1
        sod = hh * 3600 + mm * 60 + ss
        for k in range(nsat):
            j += 1
            if j >= n:
                break
            parts = lines[j].split()
            if k >= len(sats) or len(parts) < 5:
                continue
            try:
                tec  = float(parts[0])
                flag = int(parts[1])
                zn   = float(parts[3])
            except ValueError:
                continue
            if flag != 0 or tec >= 999.0 or zn >= _ROTI_MAX_ELEV_ZENITH:
                continue
            sat_tec.setdefault(sats[k], []).append((sod, tec))
        j += 1

    if not sat_tec:
        return None

    # ── TEC rate (TECU/min) per satellite, then ROTI per 5-min window ──
    # window_roti[w] = max across satellites of std(rate) in window w
    n_windows = 86400 // _ROTI_WINDOW_S
    window_max = np.full(n_windows, np.nan, dtype=np.float32)

    for samples in sat_tec.values():
        if len(samples) < 2:
            continue
        samples.sort(key=lambda s: s[0])
        t = np.array([s[0] for s in samples], dtype=np.int64)
        v = np.array([s[1] for s in samples], dtype=np.float64)
        dt = np.diff(t)
        ok = dt == 30                       # only consecutive 30-s pairs
        if not ok.any():
            continue
        rate = (np.diff(v)[ok]) * 2.0       # TECU / 30 s -> TECU/min
        tmid = t[1:][ok]
        widx = tmid // _ROTI_WINDOW_S
        for w in np.unique(widx):
            sel = rate[widx == w]
            if len(sel) < _ROTI_MIN_SAMPLES:
                continue
            roti = float(np.std(sel))
            if np.isnan(window_max[w]) or roti > window_max[w]:
                window_max[w] = roti

    # ── Bin 5-min windows into 10-min series (max of the two windows) ──
    per_bin = _ROTI_BIN_S // _ROTI_WINDOW_S
    n_bins = 86400 // _ROTI_BIN_S
    series = np.full(n_bins, np.nan, dtype=np.float32)
    for b in range(n_bins):
        chunk = window_max[b * per_bin:(b + 1) * per_bin]
        if not np.all(np.isnan(chunk)):
            series[b] = np.nanmax(chunk)

    if np.all(np.isnan(series)):
        return None
    return RotiStation(code=code, lat=lat, lon=lon, series=series)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rl1, rl2 = math.radians(lat1), math.radians(lat2)
    dlat = rl2 - rl1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rl1) * math.cos(rl2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def interpolate_roti(
    roti: Optional[RotiData], lat: float, lon: float,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """
    ROTI at (lat, lon) for the current UT time-of-day, from the nearest
    station within _ROTI_MAX_RADIUS_KM. Pure CPU — no network.
    """
    if roti is None or not roti.stations:
        return None
    now = now or datetime.now(timezone.utc)
    b = (now.hour * 3600 + now.minute * 60) // _ROTI_BIN_S

    best: Optional[float] = None
    best_dist = _ROTI_MAX_RADIUS_KM
    for st in roti.stations:
        d = _haversine_km(lat, lon, st.lat, st.lon)
        if d >= best_dist:
            continue
        v = st.series[b]
        if np.isnan(v):
            # fall back to the closest non-NaN bin within +/- 30 min
            for off in (1, -1, 2, -2, 3, -3):
                bb = b + off
                if 0 <= bb < len(st.series) and not np.isnan(st.series[bb]):
                    v = st.series[bb]
                    break
            else:
                continue
        best, best_dist = float(v), d
    return best
