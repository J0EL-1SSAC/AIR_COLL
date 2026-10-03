from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any

from pyproj import Transformer

from .models import AircraftState
from .prediction import track_to_velocity_components

_M_TO_FT = 3.280839895
_MPS_TO_KT = 1.943844492
_NM_TO_M = 1852.0


class AircraftStateManager:
    """Bounded per-aircraft state/history with stale and analysis-quality handling."""
    def __init__(self, *, latitude: float, longitude: float, history_length: int,
                 low_quality_after_s: float, drop_after_s: float, tombstone_s: float,
                 low_altitude_ft: float, smoothing_enabled: bool, alpha: float, beta: float,
                 transform: Transformer | None = None):
        self.transform = transform or Transformer.from_crs("EPSG:4326", f"+proj=aeqd +lat_0={latitude} +lon_0={longitude} +datum=WGS84 +units=m +no_defs", always_xy=True)
        self.history_length = history_length
        self.low_quality_after_s, self.drop_after_s, self.tombstone_s = low_quality_after_s, drop_after_s, tombstone_s
        self.low_altitude_m = low_altitude_ft / _M_TO_FT
        self.smoothing_enabled, self.alpha, self.beta = smoothing_enabled, alpha, beta
        self.states: dict[str, dict[str, Any]] = {}
        self.histories: dict[str, deque] = defaultdict(lambda: deque(maxlen=self.history_length))
        self.recently_lost: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _raw_velocity(state: AircraftState) -> tuple[float | None, float | None]:
        if state.velocity_mps is None or state.track_deg is None:
            return None, None
        return track_to_velocity_components(state.velocity_mps, state.track_deg)

    def _position(self, state: AircraftState) -> tuple[float, float] | None:
        if state.latitude is None or state.longitude is None:
            return None
        return self.transform.transform(state.longitude, state.latitude)

    def update(self, observations: list[AircraftState], fetch_time: float) -> None:
        present = set()
        for observation in observations:
            key = observation.icao24
            present.add(key)
            entry = self.states.get(key)
            if entry is None:
                entry = {"first_seen": fetch_time, "last_seen": fetch_time,
                         "last_position_timestamp": None, "last_position_seen": None, "filter": None}
                self.states[key] = entry
            entry["last_seen"] = fetch_time
            entry["observation"] = observation
            position = self._position(observation)
            duplicate = (observation.position_timestamp is not None and
                         observation.position_timestamp == entry["last_position_timestamp"])
            if position is not None and not duplicate:
                x_m, y_m = position
                vx, vy = self._raw_velocity(observation)
                filter_state = entry["filter"]
                if self.smoothing_enabled and vx is not None and vy is not None and filter_state is not None:
                    old_x, old_y, old_vx, old_vy, old_time = filter_state
                    dt = fetch_time - old_time
                    if dt > 0:
                        pred_x, pred_y = old_x + old_vx * dt, old_y + old_vy * dt
                        rx, ry = x_m - pred_x, y_m - pred_y
                        x_m, y_m = pred_x + self.alpha * rx, pred_y + self.alpha * ry
                        vx, vy = old_vx + self.beta * rx / dt, old_vy + self.beta * ry / dt
                if vx is not None and vy is not None:
                    entry["filter"] = (x_m, y_m, vx, vy, fetch_time)
                entry.update({"x_m": x_m, "y_m": y_m, "vx_mps": vx, "vy_mps": vy,
                              "latitude": observation.latitude, "longitude": observation.longitude,
                              "last_position_timestamp": observation.position_timestamp,
                              "last_position_seen": fetch_time,
                              "observation": observation})
                timestamp = observation.position_timestamp or fetch_time
                self.histories[key].append({"timestamp": timestamp, "latitude": observation.latitude,
                                            "longitude": observation.longitude, "x_m": x_m, "y_m": y_m})

        self.expire(fetch_time)

    def expire(self, now: float) -> None:
        for key, entry in list(self.states.items()):
            if now - entry["last_seen"] >= self.drop_after_s:
                latest = self._serialize(key, entry, now)
                self.recently_lost[key] = {"state": latest, "expires_at": now + self.tombstone_s}
                del self.states[key]
                self.histories.pop(key, None)
        for key, lost in list(self.recently_lost.items()):
            if now >= lost["expires_at"]:
                del self.recently_lost[key]

    def _serialize(self, key: str, entry: dict, now: float) -> dict:
        observation: AircraftState = entry["observation"]
        position_reference = observation.position_timestamp or entry.get("last_position_timestamp") or entry.get("last_position_seen") or entry["last_seen"]
        age_s = max(0.0, now - position_reference)
        flags = []
        if observation.latitude is None or observation.longitude is None:
            flags.append("position_missing")
        if observation.baro_altitude_m is None:
            flags.append("altitude_missing")
        if observation.velocity_mps is None or observation.track_deg is None:
            flags.append("velocity_missing")
        if observation.on_ground is None:
            flags.append("on_ground_ambiguous")
        if observation.position_timestamp is None:
            flags.append("position_timestamp_missing")
        if now - entry["last_seen"] >= self.low_quality_after_s or age_s >= self.low_quality_after_s:
            flags.extend(["stale_position", "LOW_QUALITY"])
        quality_complete = not any(flag in flags for flag in ("position_missing", "altitude_missing", "velocity_missing", "stale_position"))
        x_m, y_m = entry.get("x_m"), entry.get("y_m")
        vx, vy = entry.get("vx_mps"), entry.get("vy_mps")
        return {"icao24": key, "callsign": observation.callsign,
                "latitude": entry.get("latitude"), "longitude": entry.get("longitude"),
                "baro_altitude_m": observation.baro_altitude_m, "geo_altitude_m": observation.geo_altitude_m,
                "velocity_mps": observation.velocity_mps, "track_deg": observation.track_deg,
                "vertical_rate_mps": observation.vertical_rate_mps, "on_ground": observation.on_ground,
                "position_timestamp": observation.position_timestamp, "last_contact": observation.last_contact,
                "first_seen": entry["first_seen"], "last_seen": entry["last_seen"], "age_s": age_s,
                "x_m": x_m, "y_m": y_m, "vx_mps": vx, "vy_mps": vy,
                "analysis_x_m": x_m + vx * age_s if quality_complete and x_m is not None and vx is not None else None,
                "analysis_y_m": y_m + vy * age_s if quality_complete and y_m is not None and vy is not None else None,
                "analysis_eligible": quality_complete,
                "altitude_ft": None if observation.baro_altitude_m is None else observation.baro_altitude_m * _M_TO_FT,
                "speed_kt": None if observation.velocity_mps is None else observation.velocity_mps * _MPS_TO_KT,
                "distance_nm": None if x_m is None or y_m is None else math.hypot(x_m, y_m) / _NM_TO_M,
                "quality_flags": flags, "history": list(self.histories.get(key, ())) }

    def snapshot(self, now: float, *, include_unpositioned: bool = False) -> list[dict]:
        result = []
        for key, entry in self.states.items():
            view = self._serialize(key, entry, now)
            if include_unpositioned or (view["latitude"] is not None and view["longitude"] is not None):
                result.append(view)
        return result

    def coverage_counts(self, now: float) -> dict:
        active = self.snapshot(now)
        return {"aircraft_count": len(active),
                "below_threshold_count": sum(1 for a in active if a["baro_altitude_m"] is not None and a["baro_altitude_m"] < self.low_altitude_m),
                "on_ground_count": sum(1 for a in active if a["on_ground"] is True),
                "low_or_ground_count": sum(1 for a in active if a["on_ground"] is True or
                                            (a["baro_altitude_m"] is not None and a["baro_altitude_m"] < self.low_altitude_m)),
                "mean_data_age_s": sum(a["age_s"] for a in active) / len(active) if active else None}
