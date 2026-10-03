"""Pure closest-point-of-approach calculations in a local ENU frame."""
from __future__ import annotations

import math


def select_altitude_basis(aircraft_a: dict, aircraft_b: dict, policy: str) -> tuple[str, float | None, float | None]:
    """Select a common altitude source for both aircraft; never mix datums."""
    choices = {
        "common_geometric_then_barometric": (("geometric", "geo_altitude_m"), ("barometric", "baro_altitude_m")),
        "common_barometric_then_geometric": (("barometric", "baro_altitude_m"), ("geometric", "geo_altitude_m")),
        "geometric_only": (("geometric", "geo_altitude_m"),),
        "barometric_only": (("barometric", "baro_altitude_m"),),
    }
    if policy not in choices:
        raise ValueError(f"Unsupported altitude basis policy: {policy}")
    for basis, field in choices[policy]:
        altitude_a, altitude_b = aircraft_a.get(field), aircraft_b.get(field)
        if altitude_a is not None and altitude_b is not None:
            return basis, float(altitude_a), float(altitude_b)
    return "unavailable", None, None


def calculate_cpa(*, x_a_m: float, y_a_m: float, vx_a_mps: float, vy_a_mps: float,
                  x_b_m: float, y_b_m: float, vx_b_mps: float, vy_b_mps: float,
                  altitude_a_m: float | None = None, altitude_b_m: float | None = None,
                  vertical_rate_a_mps: float | None = None, vertical_rate_b_mps: float | None = None,
                  altitude_basis: str = "unavailable", lookahead_s: float,
                  relative_speed_epsilon_mps: float, position_epsilon_m: float) -> dict:
    """Compute 2D relative-motion CPA, clamped future time, and optional vertical metrics."""
    values = (x_a_m, y_a_m, vx_a_mps, vy_a_mps, x_b_m, y_b_m, vx_b_mps, vy_b_mps,
              lookahead_s, relative_speed_epsilon_mps, position_epsilon_m)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("CPA coordinates, velocities, and thresholds must be finite")
    if lookahead_s < 0 or relative_speed_epsilon_mps < 0 or position_epsilon_m < 0:
        raise ValueError("CPA thresholds must be non-negative")

    rx, ry = x_b_m - x_a_m, y_b_m - y_a_m
    rvx, rvy = vx_b_mps - vx_a_mps, vy_b_mps - vy_a_mps
    separation_squared = rx * rx + ry * ry
    relative_speed_squared = rvx * rvx + rvy * rvy
    dot = rx * rvx + ry * rvy
    current_h_sep = math.sqrt(separation_squared)
    if relative_speed_squared <= relative_speed_epsilon_mps ** 2:
        t_cpa_raw = 0.0
    else:
        t_cpa_raw = -dot / relative_speed_squared
    t_cpa = min(lookahead_s, max(0.0, t_cpa_raw))
    cpa_a_x, cpa_a_y = x_a_m + vx_a_mps * t_cpa, y_a_m + vy_a_mps * t_cpa
    cpa_b_x, cpa_b_y = x_b_m + vx_b_mps * t_cpa, y_b_m + vy_b_mps * t_cpa
    h_sep_cpa = math.hypot(cpa_b_x - cpa_a_x, cpa_b_y - cpa_a_y)
    closing_speed = 0.0 if current_h_sep <= position_epsilon_m else -dot / current_h_sep

    current_v_sep = None
    cpa_v_sep = None
    vertical_reason = None
    if altitude_a_m is None or altitude_b_m is None:
        vertical_reason = "NO_COMMON_ALTITUDE_BASIS"
    else:
        current_v_sep = abs(altitude_b_m - altitude_a_m)
        if vertical_rate_a_mps is None or vertical_rate_b_mps is None:
            vertical_reason = "VERTICAL_RATE_UNAVAILABLE"
        else:
            alt_a_cpa = altitude_a_m + vertical_rate_a_mps * t_cpa
            alt_b_cpa = altitude_b_m + vertical_rate_b_mps * t_cpa
            cpa_v_sep = abs(alt_b_cpa - alt_a_cpa)

    return {
        "t_cpa_s": t_cpa,
        "t_cpa_raw_s": t_cpa_raw,
        "h_sep_cpa_m": h_sep_cpa,
        "v_sep_cpa_m": cpa_v_sep,
        "vertical_separation_reason": vertical_reason,
        "closing_speed_mps": closing_speed,
        "converging": dot < 0.0,
        "cpa_in_past": t_cpa_raw < 0.0,
        "current_h_sep_m": current_h_sep,
        "current_v_sep_m": current_v_sep,
        "altitude_basis": altitude_basis,
        "cpa_a_enu": {"x_m": cpa_a_x, "y_m": cpa_a_y},
        "cpa_b_enu": {"x_m": cpa_b_x, "y_m": cpa_b_y},
    }
