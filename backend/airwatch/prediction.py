"""Explainable constant-velocity trajectory prediction in the airport ENU frame."""
from __future__ import annotations

import math
from typing import Mapping, Protocol, Sequence

from pyproj import Transformer

from .clock import Clock


class Predictor(Protocol):
    def predict(self, state: Mapping, horizons: Sequence[float]) -> dict: ...


def track_to_velocity_components(speed_mps: float, track_deg: float) -> tuple[float, float]:
    """Convert ADS-B true track (clockwise from north) to ENU east/north velocity."""
    angle_rad = math.radians(track_deg)
    return speed_mps * math.sin(angle_rad), speed_mps * math.cos(angle_rad)


class ConstantVelocityPredictor:
    """Predict along a straight ENU path using source-clock age compensation."""

    def __init__(self, *, clock: Clock, inverse_transformer: Transformer, settings: Mapping):
        self.clock = clock
        self.inverse_transformer = inverse_transformer
        self.ground_elevation_m = float(settings["ground_elevation_m"])
        self.max_data_age_s = float(settings["max_data_age_s"])
        self.skip_on_ground = bool(settings["skip_on_ground"])
        self.skip_low_quality = bool(settings["skip_low_quality"])
        self.skip_stale = bool(settings["skip_stale"])
        uncertainty = settings["uncertainty"]
        self.base_position_error_m = float(uncertainty["base_position_error_m"])
        self.speed_error_mps = float(uncertainty["speed_error_mps"])
        self.age_error_m_per_s = float(uncertainty["age_error_m_per_s"])

    def _skip_reason(self, state: Mapping, age_s: float) -> str | None:
        if self.skip_on_ground and state.get("on_ground") is True:
            return "ON_GROUND"
        if self.skip_stale and (age_s >= self.max_data_age_s or "stale_position" in state.get("quality_flags", ())):
            return "STALE"
        if "position_missing" in state.get("quality_flags", ()):
            return "MISSING_POSITION"
        if state.get("x_m") is None or state.get("y_m") is None:
            return "MISSING_POSITION"
        if state.get("velocity_mps") is None or state.get("vx_mps") is None or state.get("vy_mps") is None:
            return "MISSING_VELOCITY"
        if state.get("track_deg") is None:
            return "MISSING_TRACK"
        if self.skip_low_quality and ("LOW_QUALITY" in state.get("quality_flags", ()) or
                                      "velocity_missing" in state.get("quality_flags", ())):
            return "LOW_QUALITY"
        return None

    def predict(self, state: Mapping, horizons: Sequence[float]) -> dict:
        """Return serializable ENU/geographic positions or an explicit skip code."""
        current_time = self.clock.now()
        position_reference = state.get("position_timestamp")
        if position_reference is None:
            position_reference = state.get("last_position_seen", state.get("last_seen", current_time))
        age_s = max(0.0, current_time - float(position_reference))
        result = {"icao24": state.get("icao24"), "status": "PREDICTED", "reason_code": None,
                  "age_s": age_s, "points": []}
        reason = self._skip_reason(state, age_s)
        if reason:
            result["status"], result["reason_code"] = "SKIPPED", reason
            return result

        compensated_x = state.get("analysis_x_m")
        compensated_y = state.get("analysis_y_m")
        x0 = (float(compensated_x) if compensated_x is not None else
              float(state["x_m"]) + float(state["vx_mps"]) * age_s)
        y0 = (float(compensated_y) if compensated_y is not None else
              float(state["y_m"]) + float(state["vy_mps"]) * age_s)
        origin_longitude, origin_latitude = self.inverse_transformer.transform(x0, y0)
        altitude = state.get("geo_altitude_m")
        if altitude is None:
            altitude = state.get("baro_altitude_m")
        vertical_rate = float(state.get("vertical_rate_mps") or 0.0)
        origin_altitude = None if altitude is None else max(
            self.ground_elevation_m, float(altitude) + vertical_rate * age_s)
        result["origin"] = {"x_m": x0, "y_m": y0, "latitude": origin_latitude,
                            "longitude": origin_longitude, "altitude_m": origin_altitude}
        for horizon in horizons:
            t_s = float(horizon)
            if not math.isfinite(t_s) or t_s <= 0:
                raise ValueError("prediction horizons must be finite positive seconds")
            x_m = x0 + float(state["vx_mps"]) * t_s
            y_m = y0 + float(state["vy_mps"]) * t_s
            longitude, latitude = self.inverse_transformer.transform(x_m, y_m)
            altitude_m = None if origin_altitude is None else max(
                self.ground_elevation_m, origin_altitude + vertical_rate * t_s)
            uncertainty_m = (self.base_position_error_m + self.speed_error_mps * t_s +
                             self.age_error_m_per_s * age_s)
            result["points"].append({"t_s": t_s, "x_m": x_m, "y_m": y_m,
                                     "latitude": latitude, "longitude": longitude,
                                     "altitude_m": altitude_m,
                                     "uncertainty_radius_m": uncertainty_m})
        return result


def build_predictor(*, model: str, clock: Clock, inverse_transformer: Transformer,
                    settings: Mapping) -> Predictor:
    if model != "constant_velocity":
        raise ValueError(f"Unsupported trajectory prediction model: {model}")
    return ConstantVelocityPredictor(clock=clock, inverse_transformer=inverse_transformer,
                                     settings=settings)
