"""Basic sanity checks for the risk scoring engine."""

from datetime import datetime, timezone

import pytest

from app.core.risk_engine import RiskLevel, compute_risk
from app.models.space_weather import DstData, F107Data, KpData, SpaceWeatherSnapshot

NOW = datetime.now(timezone.utc)


def _snapshot(kp=2.0, dst=-10.0, f107=90.0) -> SpaceWeatherSnapshot:
    return SpaceWeatherSnapshot(
        fetched_at=NOW,
        kp=KpData(timestamp=NOW, kp=2, kp_fraction=kp),
        dst=DstData(timestamp=NOW, dst_nt=dst),
        f107=F107Data(timestamp=NOW, f107_sfu=f107),
    )


def test_quiet_conditions_green():
    result = compute_risk(_snapshot(kp=1.0, dst=-5.0, f107=80.0))
    assert result.level == RiskLevel.LOW
    assert result.score < 0.3


def test_moderate_storm_yellow():
    result = compute_risk(_snapshot(kp=4.5, dst=-60.0, f107=120.0))
    assert result.level == RiskLevel.MEDIUM


def test_severe_storm_red():
    result = compute_risk(_snapshot(kp=8.0, dst=-200.0, f107=180.0))
    assert result.level == RiskLevel.HIGH
    assert result.score >= 0.6


def test_score_bounded():
    result = compute_risk(_snapshot(kp=9.0, dst=-500.0, f107=300.0))
    assert 0.0 <= result.score <= 1.0
