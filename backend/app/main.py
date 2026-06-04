import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from app.collectors.embrace import (
    EmbraceMatrices,
    brazil_heatmap_grid,
    fetch_embrace_matrices,
    interpolate_embrace,
)
from app.collectors.gfz import fetch_dst
from app.collectors.noaa import fetch_f107, fetch_kp
from app.core.risk_engine import compute_risk, point_score
from app.models.space_weather import SpaceWeatherSnapshot

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
# Silence noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.INFO)
log = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────────────────
_cache: SpaceWeatherSnapshot | None = None
_embrace: EmbraceMatrices | None = None
_heatmap_cache: dict | None = None          # {data: ..., cached_at: datetime}
_REFRESH_INTERVAL = 15 * 60  # seconds


async def _refresh_loop():
    """Background task: refresh all external data every 15 minutes."""
    async with httpx.AsyncClient() as client:
        while True:
            global _cache, _embrace

            # ── Geomagnetic / solar indices (NOAA + Kyoto) ────────────────────
            snapshot = SpaceWeatherSnapshot(fetched_at=datetime.now(timezone.utc))

            try:
                snapshot.kp = await fetch_kp(client)
            except Exception as exc:
                log.warning("Kp fetch failed (%s): %s", type(exc).__name__, exc, exc_info=True)
                snapshot.errors.append(f"kp [{type(exc).__name__}]: {exc}")

            try:
                snapshot.dst = await fetch_dst(client)
            except Exception as exc:
                log.warning("Dst fetch failed (%s): %s", type(exc).__name__, exc, exc_info=True)
                snapshot.errors.append(f"dst [{type(exc).__name__}]: {exc}")

            try:
                snapshot.f107 = await fetch_f107(client)
            except Exception as exc:
                log.warning("F10.7 fetch failed (%s): %s", type(exc).__name__, exc, exc_info=True)
                snapshot.errors.append(f"f107 [{type(exc).__name__}]: {exc}")

            _cache = snapshot
            log.info(
                "Geomagnetic cache refreshed — Kp=%.2f Dst=%.0f F10.7=%.0f",
                snapshot.kp.kp_fraction if snapshot.kp else float("nan"),
                snapshot.dst.dst_nt     if snapshot.dst  else float("nan"),
                snapshot.f107.f107_sfu  if snapshot.f107 else float("nan"),
            )

            # ── EMBRACE ionospheric matrices ───────────────────────────────────
            try:
                _embrace = await fetch_embrace_matrices(client)
            except Exception as exc:
                log.warning("EMBRACE fetch failed (%s): %s", type(exc).__name__, exc, exc_info=True)
                if snapshot.errors is not None:
                    snapshot.errors.append(f"embrace [{type(exc).__name__}]: {exc}")

            await asyncio.sleep(_REFRESH_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_refresh_loop())
    yield
    task.cancel()


app = FastAPI(title="Clima Solar API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/status")
async def get_status(
    lat: float = Query(..., ge=-90, le=90,    description="Latitude (decimal degrees)"),
    lon: float = Query(..., ge=-180, le=180,  description="Longitude (decimal degrees)"),
):
    if _cache is None:
        return {
            "status": "initializing",
            "message": "First data fetch in progress — retry in 30 s",
        }

    log.debug("── /status request lat=%.5f lon=%.5f ─────────────────────────", lat, lon)

    # Interpolate EMBRACE matrices for requested location (CPU only, no network)
    if _embrace:
        s4_loaded  = _embrace.s4_matrix  is not None
        phi_loaded = _embrace.phi_matrix is not None
        log.debug("   EMBRACE matrices: S4=%s (map@%s) | phi=%s (map@%s)",
                  "loaded" if s4_loaded  else "missing",
                  _embrace.s4_map_time.strftime("%H:%Mz")  if _embrace.s4_map_time  else "?",
                  "loaded" if phi_loaded else "missing",
                  _embrace.phi_map_time.strftime("%H:%Mz") if _embrace.phi_map_time else "?")
    else:
        log.debug("   EMBRACE matrices: not yet fetched")

    embrace = interpolate_embrace(_embrace, lat, lon) if _embrace else None

    if embrace:
        log.debug("   EMBRACE interpolated: S4=%s phi60=%s",
                  f"{embrace.s4:.5f}"       if embrace.s4       is not None else "null (no data at pixel)",
                  f"{embrace.phi60_rad:.5f}" if embrace.phi60_rad is not None else "null (no data at pixel)")
    else:
        log.debug("   EMBRACE interpolated: None (outside coverage or matrices absent)")

    risk = compute_risk(_cache, embrace)

    embrace_age_min = None
    if _embrace and _embrace.s4_map_time:
        embrace_age_min = round(
            (datetime.now(timezone.utc) - _embrace.s4_map_time).total_seconds() / 60
        )

    return {
        "fetched_at": _cache.fetched_at.isoformat(),
        "location": {"lat": lat, "lon": lon},
        "risk": {
            "level": risk.level.value,
            "score": risk.score,
            "breakdown": {
                "kp":    risk.kp_score,
                "dst":   risk.dst_score,
                "f107":  risk.f107_score,
                "s4":    risk.s4_score,
                "phi60": risk.phi60_score,
                "roti":  risk.roti_score,
                "vtec":  risk.vtec_score,
            },
        },
        "raw": {
            "kp":        _cache.kp.kp_fraction  if _cache.kp   else None,
            "dst_nt":    _cache.dst.dst_nt       if _cache.dst  else None,
            "f107_sfu":  _cache.f107.f107_sfu    if _cache.f107 else None,
            "s4":        embrace.s4              if embrace     else None,
            "phi60_rad": embrace.phi60_rad       if embrace     else None,
        },
        "data_age": {
            "geomagnetic_min": round(
                (datetime.now(timezone.utc) - _cache.fetched_at).total_seconds() / 60
            ),
            "embrace_map_min": embrace_age_min,
        },
        "errors": _cache.errors,
    }


@app.get("/heatmap")
async def get_heatmap():
    """
    Ionospheric risk heatmap over Brazil at 1.5° resolution.
    ~900 grid points with {lat, lon, s4, score}.
    Cached for 15 min server-side.
    """
    global _heatmap_cache

    now = datetime.now(timezone.utc)
    if _heatmap_cache and (now - _heatmap_cache["cached_at"]).total_seconds() < 900:
        return _heatmap_cache["data"]

    if _cache is None:
        return {"timestamp": None, "points": []}

    # Global geo raw values (same for all grid points)
    kp_raw   = _cache.kp.kp_fraction  if _cache.kp   else None
    dst_raw  = _cache.dst.dst_nt      if _cache.dst  else None
    f107_raw = _cache.f107.f107_sfu   if _cache.f107 else None

    grid = brazil_heatmap_grid(_embrace)   # ~900 points, fast CPU loop

    points = []
    for pt in grid:
        score = point_score(
            kp_raw, dst_raw, f107_raw,
            s4_raw=pt["s4"], phi60_raw=pt["phi60"],
        )
        points.append({
            "lat":   pt["lat"],
            "lon":   pt["lon"],
            "s4":    round(pt["s4"], 4) if pt["s4"] is not None else None,
            "score": score,
        })

    result = {"timestamp": _cache.fetched_at.isoformat(), "points": points}
    _heatmap_cache = {"data": result, "cached_at": now}
    log.info("Heatmap computed: %d points (embrace=%s)", len(points),
             "ok" if _embrace and _embrace.s4_matrix is not None else "unavailable")
    return result


@app.get("/health")
async def health():
    return {
        "ok": True,
        "cache_age_s": (
            (datetime.now(timezone.utc) - _cache.fetched_at).total_seconds()
            if _cache else None
        ),
        "embrace_loaded": _embrace is not None and (
            _embrace.s4_matrix is not None or _embrace.phi_matrix is not None
        ),
    }
