"""
Composite GNSS risk score engine.

Each parameter is normalized to a 0–1 sub-score, then combined into a
weighted composite. Returns a RiskResult with the final level and scores.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.models.space_weather import SpaceWeatherSnapshot

log = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    LOW    = "low"     # score < 0.3  — green
    MEDIUM = "medium"  # 0.3 ≤ score < 0.6 — yellow
    HIGH   = "high"    # score ≥ 0.6  — red


@dataclass
class RiskResult:
    level:       RiskLevel
    score:       float
    kp_score:    float
    dst_score:   float
    f107_score:  float
    s4_score:    Optional[float]
    phi60_score: Optional[float]
    roti_score:  Optional[float]
    vtec_score:  Optional[float]


_WEIGHTS = {
    "kp":   0.20,
    "dst":  0.15,
    "f107": 0.15,
    "s4":   0.175,
    "phi60":0.125,
    "roti": 0.10,
    "vtec": 0.10,
}


def _kp_score(kp: float) -> float:
    if kp < 4:   return kp / 4 * 0.3
    if kp < 6:   return 0.3 + (kp - 4) / 2 * 0.3
    return min(0.6 + (kp - 6) / 3 * 0.4, 1.0)

def _dst_score(dst: float) -> float:
    if dst > -30:   return 0.0
    if dst >= -100: return 0.3 + (-dst - 30) / 70 * 0.3
    return min(0.6 + (-dst - 100) / 200 * 0.4, 1.0)

def _f107_score(f107: float) -> float:
    if f107 < 100: return f107 / 100 * 0.3
    if f107 < 150: return 0.3 + (f107 - 100) / 50 * 0.3
    return min(0.6 + (f107 - 150) / 100 * 0.4, 1.0)

def _s4_score(s4: float) -> float:
    if s4 < 0.3: return s4 / 0.3 * 0.3
    if s4 < 0.6: return 0.3 + (s4 - 0.3) / 0.3 * 0.3
    return min(0.6 + (s4 - 0.6) / 0.4 * 0.4, 1.0)

def _phi60_score(phi60: float) -> float:
    if phi60 < 0.1: return phi60 / 0.1 * 0.3
    if phi60 < 0.5: return 0.3 + (phi60 - 0.1) / 0.4 * 0.3
    return min(0.6 + (phi60 - 0.5) / 0.5 * 0.4, 1.0)

def _roti_score(roti: float) -> float:
    if roti < 0.5: return roti / 0.5 * 0.3
    if roti < 1.5: return 0.3 + (roti - 0.5) / 1.0 * 0.3
    return min(0.6 + (roti - 1.5) / 1.5 * 0.4, 1.0)

def _vtec_score(vtec: float) -> float:
    if vtec < 20: return vtec / 20 * 0.3
    if vtec < 60: return 0.3 + (vtec - 20) / 40 * 0.3
    return min(0.6 + (vtec - 60) / 60 * 0.4, 1.0)


def compute_risk(snapshot: SpaceWeatherSnapshot, embrace_data=None) -> RiskResult:
    # ── Sub-scores ────────────────────────────────────────────────────────────
    kp_raw   = snapshot.kp.kp_fraction  if snapshot.kp   else None
    dst_raw  = snapshot.dst.dst_nt      if snapshot.dst  else None
    f107_raw = snapshot.f107.f107_sfu   if snapshot.f107 else None
    s4_raw   = embrace_data.s4          if embrace_data and embrace_data.s4          is not None else None
    phi60_raw= embrace_data.phi60_rad   if embrace_data and embrace_data.phi60_rad   is not None else None
    roti_raw = embrace_data.roti_tecu_min if embrace_data and embrace_data.roti_tecu_min is not None else None
    vtec_raw = embrace_data.vtec_tecu   if embrace_data and embrace_data.vtec_tecu   is not None else None

    kp_s    = _kp_score(kp_raw)     if kp_raw   is not None else 0.5
    dst_s   = _dst_score(dst_raw)   if dst_raw  is not None else 0.5
    f107_s  = _f107_score(f107_raw) if f107_raw is not None else 0.5
    s4_s    = _s4_score(s4_raw)     if s4_raw   is not None else None
    phi60_s = _phi60_score(phi60_raw) if phi60_raw is not None else None
    roti_s  = _roti_score(roti_raw) if roti_raw is not None else None
    vtec_s  = _vtec_score(vtec_raw) if vtec_raw is not None else None

    # ── Weighted composite ────────────────────────────────────────────────────
    # Build a dict of only the parameters that have actual values, then
    # redistribute the weight of any null param proportionally among the rest.
    available: dict[str, float] = {
        "kp":   kp_s,
        "dst":  dst_s,
        "f107": f107_s,
    }
    if s4_s    is not None: available["s4"]    = s4_s
    if phi60_s is not None: available["phi60"] = phi60_s
    if roti_s  is not None: available["roti"]  = roti_s
    if vtec_s  is not None: available["vtec"]  = vtec_s

    total_weight = sum(_WEIGHTS[k] for k in available)
    scale = 1.0 / total_weight if total_weight > 0 else 1.0
    score = sum(available[k] * _WEIGHTS[k] for k in available) * scale

    score = min(max(score, 0.0), 1.0)
    level = RiskLevel.LOW if score < 0.3 else RiskLevel.MEDIUM if score < 0.6 else RiskLevel.HIGH

    # ── Detailed log ──────────────────────────────────────────────────────────
    log.debug("── Risk computation ─────────────────────────────────────────")
    log.debug("  %-10s raw=%-10s score=%-8s weight=%.2f  contrib=%.4f",
              "Kp",    f"{kp_raw}"   if kp_raw   is not None else "fallback(0.5)",
              f"{kp_s:.4f}", _WEIGHTS["kp"],   kp_s * _WEIGHTS["kp"])
    log.debug("  %-10s raw=%-10s score=%-8s weight=%.2f  contrib=%.4f",
              "Dst",   f"{dst_raw}"  if dst_raw  is not None else "fallback(0.5)",
              f"{dst_s:.4f}", _WEIGHTS["dst"],  dst_s * _WEIGHTS["dst"])
    log.debug("  %-10s raw=%-10s score=%-8s weight=%.2f  contrib=%.4f",
              "F10.7", f"{f107_raw}" if f107_raw is not None else "fallback(0.5)",
              f"{f107_s:.4f}", _WEIGHTS["f107"], f107_s * _WEIGHTS["f107"])

    for param, raw, sub in [
        ("S4",    s4_raw,    s4_s),
        ("phi60", phi60_raw, phi60_s),
        ("ROTI",  roti_raw,  roti_s),
        ("VTEC",  vtec_raw,  vtec_s),
    ]:
        key = param.lower()
        if sub is not None:
            log.debug("  %-10s raw=%-10s score=%-8s weight=%.3f contrib=%.4f",
                      param, f"{raw:.4f}", f"{sub:.4f}",
                      _WEIGHTS[key], sub * _WEIGHTS[key])
        else:
            log.debug("  %-10s raw=None       score=None     weight=%.3f (redistributed)",
                      param, _WEIGHTS[key])
    log.debug("  mode: %s | active_params=%d | scale=%.4f",
              "full" if len(available) == 7 else "partial", len(available), scale)

    log.info("✓ Risk score=%.4f level=%s  [Kp=%.2f Dst=%s F10.7=%s S4=%s phi60=%s]",
             score, level.value,
             kp_raw   if kp_raw   is not None else float("nan"),
             f"{dst_raw:.0f}" if dst_raw is not None else "?",
             f"{f107_raw:.0f}" if f107_raw is not None else "?",
             f"{s4_raw:.4f}"   if s4_raw  is not None else "N/A",
             f"{phi60_raw:.4f}" if phi60_raw is not None else "N/A")

    return RiskResult(
        level=level, score=round(score, 4),
        kp_score=round(kp_s, 4), dst_score=round(dst_s, 4), f107_score=round(f107_s, 4),
        s4_score=round(s4_s, 4)       if s4_s    is not None else None,
        phi60_score=round(phi60_s, 4) if phi60_s is not None else None,
        roti_score=round(roti_s, 4)   if roti_s  is not None else None,
        vtec_score=round(vtec_s, 4)   if vtec_s  is not None else None,
    )
