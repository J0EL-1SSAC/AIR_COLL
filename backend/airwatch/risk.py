"""Config-driven, explainable potential-aircraft-conflict risk scoring."""
from __future__ import annotations

import math
from typing import Mapping

LEVELS = ("NORMAL", "LOW", "MEDIUM", "HIGH", "CRITICAL")
_RANK = {level: index for index, level in enumerate(LEVELS)}


def _angle_difference(a: float, b: float) -> float:
    return abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)


def _position(state: Mapping) -> tuple[float, float] | None:
    x = state.get("analysis_x_m", state.get("x_m"))
    y = state.get("analysis_y_m", state.get("y_m"))
    return None if x is None or y is None else (float(x), float(y))


def confidence_for(pair: Mapping, settings: Mapping) -> str:
    age = max(float(pair.get("data_age_a_s") or 0), float(pair.get("data_age_b_s") or 0))
    uncertainty = float(pair.get("combined_uncertainty_m") or 0)
    confidence = settings["confidence"]
    if age <= float(confidence["high_max_age_s"]) and uncertainty <= float(confidence["high_max_uncertainty_m"]):
        return "HIGH"
    if age <= float(confidence["medium_max_age_s"]) and uncertainty <= float(confidence["medium_max_uncertainty_m"]):
        return "MEDIUM"
    return "LOW"


def evaluate_pair_risk(pair: Mapping, settings: Mapping, *,
                       states_by_id: Mapping[str, Mapping] | None = None,
                       profile_name: str | None = None) -> dict:
    """Return a risk level and audit trail, or a filter reason, for one CPA pair."""
    reasons: list[str] = []
    filters = settings["filters"]
    states = states_by_id or {}
    a_id, b_id = pair["aircraft_a"]["icao24"], pair["aircraft_b"]["icao24"]
    state_a, state_b = states.get(a_id, {}), states.get(b_id, {})

    if filters["ignore_both_on_ground"] and state_a.get("on_ground") is True and state_b.get("on_ground") is True:
        return {"level": "NORMAL", "score_components": {}, "reasons": [], "filtered_reason": "both_on_ground"}
    if filters["ignore_diverging"] and (not pair.get("converging") or pair.get("cpa_in_past")):
        return {"level": "NORMAL", "score_components": {}, "reasons": [], "filtered_reason": "diverging"}
    if filters["ignore_beyond_lookahead"] and float(pair.get("t_cpa_raw_s", pair["t_cpa_s"])) > float(settings["lookahead_s"]):
        return {"level": "NORMAL", "score_components": {}, "reasons": [], "filtered_reason": "beyond_lookahead"}

    age_a, age_b = float(pair.get("data_age_a_s") or 0), float(pair.get("data_age_b_s") or 0)
    max_age = max(age_a, age_b)
    if filters["ignore_old_data"] and max_age > float(settings["max_data_age_for_alert_s"]):
        return {"level": "NORMAL", "score_components": {"max_data_age_s": max_age},
                "reasons": [], "filtered_reason": "data_too_old"}

    profile = settings["profiles"][profile_name or settings["active_profile"]]
    raw_horizontal = float(pair["h_sep_cpa_m"])
    uncertainty = float(pair.get("combined_uncertainty_m") or 0.0)
    uncertainty_factor = float(profile["uncertainty_factor"])
    adjusted_horizontal = max(0.0, raw_horizontal - uncertainty_factor * uncertainty)
    vertical = pair.get("v_sep_cpa_m")
    time_to_cpa = float(pair["t_cpa_s"])
    if vertical is None and settings["treat_unknown_vertical_as"] == "skip":
        return {"level": "NORMAL", "score_components": {"adjusted_horizontal_m": adjusted_horizontal},
                "reasons": ["Vertical separation is unavailable."], "filtered_reason": "vertical_unavailable"}

    score_components = {"raw_horizontal_m": raw_horizontal,
                        "combined_uncertainty_m": uncertainty,
                        "uncertainty_factor": uncertainty_factor,
                        "adjusted_horizontal_m": adjusted_horizontal,
                        "vertical_m": vertical, "time_to_cpa_s": time_to_cpa,
                        "closing_speed_mps": float(pair.get("closing_speed_mps") or 0),
                        "max_data_age_s": max_age, "altitude_basis": pair.get("altitude_basis", "unavailable")}
    if vertical is None:
        reasons.append("Vertical separation is unavailable; conservative policy evaluates horizontal separation and time.")
    if uncertainty_factor and uncertainty:
        reasons.append(f"Horizontal separation was reduced by {uncertainty_factor * uncertainty:.1f} m for modeled uncertainty.")

    selected = "NORMAL"
    for level in LEVELS[1:]:
        limit = profile["levels"][level]
        vertical_ok = vertical is None or float(vertical) <= float(limit["max_vertical_separation_m"])
        checks = {"horizontal_within_limit": adjusted_horizontal <= float(limit["max_horizontal_separation_m"]),
                  "vertical_within_limit": vertical_ok,
                  "time_within_limit": time_to_cpa <= float(limit["max_time_to_cpa_s"])}
        score_components[f"{level.lower()}_checks"] = checks
        if all(checks.values()):
            selected = level
    if selected == "NORMAL":
        reasons.append("CPA metrics did not meet the LOW profile thresholds.")
    else:
        reasons.append(f"All configured {selected} threshold checks were met.")

    adjustments = profile["adjustments"]
    closing_limit = adjustments.get("closing_speed_escalation_mps")
    if selected != "NORMAL" and closing_limit is not None and float(pair.get("closing_speed_mps") or 0) >= float(closing_limit):
        selected = LEVELS[min(_RANK[selected] + int(adjustments["closing_speed_raise_levels"]), _RANK["CRITICAL"])]
        reasons.append(f"Closing speed met the escalation threshold ({float(closing_limit):g} m/s).")
    poor_flags = set(adjustments.get("poor_data_quality_flags", ()))
    observed_flags = set(state_a.get("quality_flags", ())) | set(state_b.get("quality_flags", ()))
    poor_age = max_age > float(adjustments["poor_data_age_s"])
    poor_quality = bool(poor_flags & observed_flags)
    if adjustments["downgrade_poor_data"] and selected != "NORMAL" and (poor_age or poor_quality):
        selected = LEVELS[max(0, _RANK[selected] - 1)]
        causes = []
        if poor_age:
            causes.append("report age")
        if poor_quality:
            causes.append("data quality flags")
        reasons.append(f"Risk was downgraded one level because of {' and '.join(causes)}.")
    if vertical is None and settings["treat_unknown_vertical_as"] == "downgrade_one_level" and selected != "NORMAL":
        selected = LEVELS[max(0, _RANK[selected] - 1)]
        reasons.append("Risk was downgraded one level because vertical separation is unavailable.")

    in_trail = profile.get("in_trail", {})
    if selected != "NORMAL" and in_trail.get("enabled"):
        track_a, track_b = state_a.get("track_deg"), state_b.get("track_deg")
        pos_a, pos_b = _position(state_a), _position(state_b)
        if track_a is not None and track_b is not None and pos_a and pos_b:
            mean_track = math.radians(float(track_a))
            dx, dy = pos_b[0] - pos_a[0], pos_b[1] - pos_a[1]
            lateral_offset = abs(dx * math.cos(mean_track) - dy * math.sin(mean_track))
            same_track = _angle_difference(track_a, track_b) <= float(in_trail["max_track_difference_deg"])
            score_components["in_trail"] = {"track_difference_deg": _angle_difference(track_a, track_b),
                                             "lateral_offset_m": lateral_offset}
            if same_track and lateral_offset > float(in_trail["min_lateral_offset_m"]):
                selected = LEVELS[max(0, _RANK[selected] - int(in_trail["downgrade_levels"]))]
                reasons.append("Risk was downgraded for same-direction in-trail geometry.")

    return {"level": selected, "score_components": score_components,
            "reasons": reasons, "filtered_reason": None,
            "confidence": confidence_for(pair, settings)}


def validate_timing_settings(config: Mapping) -> list[str]:
    """Return startup warnings for age thresholds that are no longer than polling."""
    poll = float(config["collector"]["poll_interval_s"])
    risk = config["risk"]
    checks = {"risk.max_data_age_for_alert_s": risk["max_data_age_for_alert_s"],
              "risk.confidence.high_max_age_s": risk["confidence"]["high_max_age_s"],
              "risk.confidence.medium_max_age_s": risk["confidence"]["medium_max_age_s"],
              "prediction.max_data_age_s": config["prediction"]["max_data_age_s"],
              "state_manager.low_quality_after_s": config["state_manager"]["low_quality_after_s"]}
    for profile_name, profile in risk["profiles"].items():
        checks[f"risk.profiles.{profile_name}.poor_data_age_s"] = profile["adjustments"]["poor_data_age_s"]
    return [f"{name}={float(value):g}s is not greater than collector.poll_interval_s={poll:g}s."
            for name, value in checks.items() if float(value) <= poll]
