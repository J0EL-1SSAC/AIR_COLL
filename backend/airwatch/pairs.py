"""Spatially pre-filtered pair selection and CPA serialization."""
from __future__ import annotations

import itertools
import math
from collections import defaultdict
from typing import Mapping, Sequence

from pyproj import Transformer

from .cpa import calculate_cpa, select_altitude_basis
from .prediction import uncertainty_radius_m

_NM_TO_M = 1852.0
_M_TO_FT = 3.280839895


def _state_position(state: Mapping) -> tuple[float, float] | None:
    x_m, y_m = state.get("analysis_x_m"), state.get("analysis_y_m")
    if x_m is not None and y_m is not None:
        return float(x_m), float(y_m)
    if state.get("x_m") is None or state.get("y_m") is None:
        return None
    x_m, y_m = float(state["x_m"]), float(state["y_m"])
    if state.get("vx_mps") is not None and state.get("vy_mps") is not None:
        age_s = float(state.get("age_s") or 0.0)
        x_m += float(state["vx_mps"]) * age_s
        y_m += float(state["vy_mps"]) * age_s
    return x_m, y_m


def _combinations(count: int) -> int:
    return count * (count - 1) // 2


def _state_rejection(state: Mapping, *, radius_m: float, max_age_s: float,
                     minimum_quality: str) -> str | None:
    position = _state_position(state)
    if position is None or "position_missing" in state.get("quality_flags", ()):
        return "missing_position"
    if math.hypot(*position) > radius_m:
        return "outside_geofence"
    if state.get("on_ground") is not False:
        return "not_airborne"
    flags = set(state.get("quality_flags", ()))
    if (float(state.get("age_s") or 0.0) >= max_age_s or
            "LOW_QUALITY" in flags or "stale_position" in flags):
        return "stale_or_low_quality"
    if minimum_quality != "not_low_quality":
        raise ValueError(f"Unsupported CPA minimum_data_quality: {minimum_quality}")
    if any(state.get(key) is None for key in ("velocity_mps", "track_deg", "vx_mps", "vy_mps")):
        return "missing_velocity"
    return None


def compute_pair_cpas(states: Sequence[Mapping], *, airport_radius_nm: float,
                      cpa_settings: Mapping, prediction_settings: Mapping,
                      inverse_transformer: Transformer) -> dict:
    """Return closest-approach records and first-failure filter counts.

    A uniform ENU grid uses the configured horizontal cutoff as its cell width.
    Only tracks in the same or adjacent cells reach CPA math, avoiding all-pairs
    geometric work while preserving every possible in-cutoff pair.
    """
    ordered_states = sorted(states, key=lambda state: str(state.get("icao24", "")).lower())
    total_pairs = _combinations(len(ordered_states))
    filter_names = ("missing_position", "outside_geofence", "not_airborne", "stale_or_low_quality",
                    "missing_velocity", "current_distance", "altitude_band", "lookahead", "cpa_distance")
    filtered = {name: 0 for name in filter_names}

    radius_m = float(airport_radius_nm) * _NM_TO_M
    cutoff_m = float(cpa_settings["horizontal_cutoff_nm"]) * _NM_TO_M
    if cutoff_m <= 0:
        raise ValueError("cpa.horizontal_cutoff_nm must be positive")
    altitude_band_m = float(cpa_settings["altitude_band_ft"]) / _M_TO_FT
    lookahead_s = float(cpa_settings["lookahead_s"])
    max_age_s = float(prediction_settings["max_data_age_s"])
    reasons = {"missing_position": [], "outside_geofence": [], "not_airborne": [],
               "stale_or_low_quality": [], "missing_velocity": []}
    eligible = []
    for state in ordered_states:
        reason = _state_rejection(state, radius_m=radius_m, max_age_s=max_age_s,
                                  minimum_quality=str(cpa_settings["minimum_data_quality"]))
        if reason is None:
            eligible.append(state)
        else:
            reasons[reason].append(state)

    # Count pairs excluded by state-level filters, assigning each pair to its first failure.
    remaining = len(ordered_states)
    for reason, rejected in reasons.items():
        count = len(rejected)
        filtered[reason] += _combinations(remaining) - _combinations(remaining - count)
        remaining -= count

    positions = {id(state): _state_position(state) for state in eligible}
    grid: dict[tuple[int, int], list[Mapping]] = defaultdict(list)
    for state in eligible:
        x_m, y_m = positions[id(state)]
        grid[(math.floor(x_m / cutoff_m), math.floor(y_m / cutoff_m))].append(state)

    nearby_pairs: list[tuple[Mapping, Mapping]] = []
    for cell, members in grid.items():
        cell_x, cell_y = cell
        neighbors = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbors.extend(grid.get((cell_x + dx, cell_y + dy), ()))
        for state_a in members:
            for state_b in neighbors:
                if str(state_a["icao24"]).lower() < str(state_b["icao24"]).lower():
                    nearby_pairs.append((state_a, state_b))

    unique_pairs = {(str(a["icao24"]).lower(), str(b["icao24"]).lower()): (a, b)
                    for a, b in nearby_pairs}
    filtered["current_distance"] += _combinations(len(eligible)) - len(unique_pairs)
    results = []

    for state_a, state_b in unique_pairs.values():
        x_a, y_a = positions[id(state_a)]
        x_b, y_b = positions[id(state_b)]
        current_h_sep = math.hypot(x_b - x_a, y_b - y_a)
        if current_h_sep > cutoff_m:
            filtered["current_distance"] += 1
            continue

        altitude_basis, altitude_a, altitude_b = select_altitude_basis(
            state_a, state_b, str(cpa_settings["altitude_basis_policy"]))
        if altitude_a is not None and abs(altitude_b - altitude_a) > altitude_band_m:
            filtered["altitude_band"] += 1
            continue

        metrics = calculate_cpa(
            x_a_m=x_a, y_a_m=y_a, vx_a_mps=float(state_a["vx_mps"]), vy_a_mps=float(state_a["vy_mps"]),
            x_b_m=x_b, y_b_m=y_b, vx_b_mps=float(state_b["vx_mps"]), vy_b_mps=float(state_b["vy_mps"]),
            altitude_a_m=altitude_a, altitude_b_m=altitude_b,
            vertical_rate_a_mps=state_a.get("vertical_rate_mps"),
            vertical_rate_b_mps=state_b.get("vertical_rate_mps"), altitude_basis=altitude_basis,
            lookahead_s=lookahead_s,
            relative_speed_epsilon_mps=float(cpa_settings["relative_speed_epsilon_mps"]),
            position_epsilon_m=float(cpa_settings["position_epsilon_m"]))
        if metrics["t_cpa_raw_s"] > lookahead_s:
            filtered["lookahead"] += 1
            continue
        if metrics["h_sep_cpa_m"] > cutoff_m:
            filtered["cpa_distance"] += 1
            continue

        cpa_a = metrics.pop("cpa_a_enu")
        cpa_b = metrics.pop("cpa_b_enu")
        lon_a, lat_a = inverse_transformer.transform(cpa_a["x_m"], cpa_a["y_m"])
        lon_b, lat_b = inverse_transformer.transform(cpa_b["x_m"], cpa_b["y_m"])
        mid_x, mid_y = (cpa_a["x_m"] + cpa_b["x_m"]) / 2, (cpa_a["y_m"] + cpa_b["y_m"]) / 2
        mid_lon, mid_lat = inverse_transformer.transform(mid_x, mid_y)
        age_a, age_b = float(state_a.get("age_s") or 0), float(state_b.get("age_s") or 0)
        horizon = metrics["t_cpa_s"]
        uncertainty_a = uncertainty_radius_m(horizon_s=horizon, age_s=age_a, settings=prediction_settings)
        uncertainty_b = uncertainty_radius_m(horizon_s=horizon, age_s=age_b, settings=prediction_settings)
        combination = str(cpa_settings["uncertainty_combination"])
        if combination == "root_sum_square":
            combined_uncertainty = math.hypot(uncertainty_a, uncertainty_b)
        elif combination == "sum":
            combined_uncertainty = uncertainty_a + uncertainty_b
        else:
            raise ValueError(f"Unsupported uncertainty combination: {combination}")
        id_a, id_b = str(state_a["icao24"]).lower(), str(state_b["icao24"]).lower()
        results.append({
            "pair_key": f"{id_a}|{id_b}",
            "aircraft_a": {"icao24": id_a, "callsign": state_a.get("callsign")},
            "aircraft_b": {"icao24": id_b, "callsign": state_b.get("callsign")},
            **metrics,
            "combined_uncertainty_m": combined_uncertainty,
            "uncertainty_a_m": uncertainty_a,
            "uncertainty_b_m": uncertainty_b,
            "data_age_a_s": age_a,
            "data_age_b_s": age_b,
            "cpa_position": {
                "aircraft_a": {**cpa_a, "latitude": lat_a, "longitude": lon_a},
                "aircraft_b": {**cpa_b, "latitude": lat_b, "longitude": lon_b},
                "midpoint": {"x_m": mid_x, "y_m": mid_y,
                             "latitude": mid_lat, "longitude": mid_lon},
            },
        })

    results.sort(key=lambda item: (item["h_sep_cpa_m"], item["pair_key"]))
    return {"pairs_considered": total_pairs, "pairs_returned": len(results),
            "filtered_by": filtered, "pairs": results}
