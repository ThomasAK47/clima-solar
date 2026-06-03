from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class KpData:
    timestamp: datetime
    kp: float
    kp_fraction: float  # precise value (e.g. 3.33 for Kp=3+)
    source: str = "NOAA"


@dataclass
class DstData:
    timestamp: datetime
    dst_nt: float  # nanoTesla, negative = storm
    source: str = "Kyoto/NOAA"


@dataclass
class F107Data:
    timestamp: datetime
    f107_sfu: float  # solar flux units
    source: str = "NOAA"


@dataclass
class EmbraceData:
    timestamp: datetime
    lat: float
    lon: float
    s4: Optional[float] = None         # amplitude scintillation index (dimensionless)
    phi60_rad: Optional[float] = None  # phase scintillation sigma_phi (radians)
    roti_tecu_min: Optional[float] = None  # not available in map product
    vtec_tecu: Optional[float] = None      # not available in map product
    source: str = "EMBRACE/INPE"


@dataclass
class SpaceWeatherSnapshot:
    fetched_at: datetime
    kp: Optional[KpData] = None
    dst: Optional[DstData] = None
    f107: Optional[F107Data] = None
    errors: list[str] = field(default_factory=list)
