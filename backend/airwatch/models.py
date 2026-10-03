from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AirportCenter:
    icao: str
    latitude: Optional[float]
    longitude: Optional[float]


@dataclass(frozen=True)
class AircraftState:
    icao24: str
    callsign: Optional[str]
    latitude: float
    longitude: float
    baro_altitude_m: Optional[float]
    geo_altitude_m: Optional[float]
    velocity_mps: Optional[float]
    track_deg: Optional[float]
    vertical_rate_mps: Optional[float]
    on_ground: Optional[bool]
    position_timestamp: Optional[float]
    last_contact: Optional[float]
    raw_payload_json: Optional[str] = None
