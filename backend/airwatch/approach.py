"""Experimental runway approach estimates using local ENU aircraft states."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from .geometry import along_track_distance, cross_track_error, distance_to_threshold, heading_difference, point_in_corridor

MPS_TO_KT = 1.943844492
M_TO_FT = 3.280839895
M_TO_NM = 1 / 1852.0


def evaluate_approach(state: dict, end, settings: dict, now: float) -> dict:
    """Return explainable current approach evidence for one aircraft/runway end."""
    reasons: list[str] = []
    x = state.get("analysis_x_m") if state.get("analysis_x_m") is not None else state.get("x_m")
    y = state.get("analysis_y_m") if state.get("analysis_y_m") is not None else state.get("y_m")
    altitude = state.get("geo_altitude_m") if state.get("geo_altitude_m") is not None else state.get("baro_altitude_m")
    age = float(state.get("age_s") or 0.0)
    if x is None or y is None:
        return {"eligible": False, "reason": "position_unavailable"}
    landing_x = getattr(end, "landing_threshold_x_m", None)
    landing_y = getattr(end, "landing_threshold_y_m", None)
    threshold_x = landing_x if landing_x is not None else end.x_m
    threshold_y = landing_y if landing_y is not None else end.y_m
    dist = distance_to_threshold(x, y, threshold_x, threshold_y)
    along = along_track_distance(x, y, threshold_x, threshold_y, end.true_heading_deg)
    cross = cross_track_error(x, y, threshold_x, threshold_y, end.true_heading_deg)
    hdg_diff = None if state.get("track_deg") is None else heading_difference(state["track_deg"], end.true_heading_deg)
    height = None if altitude is None else altitude - (end.elevation_m or 0.0)
    speed = state.get("velocity_mps")
    speed_kt = None if speed is None else speed * MPS_TO_KT
    glide = None if height is None or along <= 0 else math.degrees(math.atan2(max(0.0, height), along))
    corridor = altitude is not None and point_in_corridor(x, y, end.corridor_polygon, altitude_m=altitude,
                                                          altitude_ceiling_m=end.altitude_ceiling_m)
    history = [p for p in state.get("history", []) if p.get("x_m") is not None and p.get("y_m") is not None
               and now - float(p.get("timestamp", now)) <= float(settings["closing_window_s"])
               and point_in_corridor(float(p["x_m"]), float(p["y_m"]), end.corridor_polygon)]
    along_values = [(float(p.get("timestamp", now)), along_track_distance(p["x_m"], p["y_m"], threshold_x, threshold_y, end.true_heading_deg)) for p in history]
    closing = len(along_values) >= int(settings["min_closing_samples"]) and along_values[-1][1] < along_values[0][1] - float(settings["min_closing_distance_m"])
    vertical_fpm = (state.get("vertical_rate_mps") or 0.0) * 60 * M_TO_FT
    descending_or_glide = vertical_fpm <= float(settings["max_vertical_rate_fpm"]) or (glide is not None and float(settings["min_glide_angle_deg"]) <= glide <= float(settings["max_glide_angle_deg"]))
    checks = {
        "inside_corridor": corridor,
        "heading_aligned": hdg_diff is not None and hdg_diff <= float(settings["max_heading_diff_deg"]),
        "moving_toward_threshold": closing,
        "descent_or_glidepath": descending_or_glide,
        "plausible_speed": speed_kt is not None and float(settings["min_speed_kt"]) <= speed_kt <= float(settings["max_speed_kt"]),
        "fresh_data": age <= float(settings["max_data_age_s"]),
        "airborne": state.get("on_ground") is not True,
        "good_quality": "LOW_QUALITY" not in (state.get("quality_flags") or []),
    }
    for label, passed in checks.items():
        if not passed:
            reasons.append(label.replace("_", " ") + " not satisfied")
    speed_error = float(settings["speed_uncertainty_mps"])
    age_margin = age * float(settings["age_distance_uncertainty_mps"])
    effective_dist = max(0.0, dist - max(0.0, speed or 0.0) * age)
    min_speed = max(float(settings["min_speed_kt"]) / MPS_TO_KT, (speed or 0.0) - speed_error)
    max_speed = min(float(settings["max_speed_kt"]) / MPS_TO_KT, (speed or 0.0) + speed_error)
    eta_min = max(0.0, effective_dist - age_margin) / max(max_speed, 0.1)
    eta_max = (dist + age_margin) / max(min_speed, 0.1)
    sample_count = len(history)
    if age > float(settings["max_data_age_s"]):
        confidence = "LOW"
    elif sample_count >= int(settings["confidence_min_samples_high"]) and checks["heading_aligned"] and checks["inside_corridor"]:
        confidence = "HIGH"
    elif sample_count >= int(settings["confidence_min_samples_medium"]):
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    return {"eligible": all(checks.values()), "checks": checks, "reasons": reasons,
            "icao24": state["icao24"], "callsign": state.get("callsign"), "runway_end": end.identifier,
            "runway_true_heading_deg": end.true_heading_deg, "distance_to_threshold_m": dist,
            "distance_to_threshold_nm": dist * M_TO_NM, "height_above_threshold_m": height,
            "height_above_threshold_ft": None if height is None else height * M_TO_FT,
            "altitude_m": altitude,
            "cross_track_error_m": cross, "along_track_distance_m": along,
            "heading_difference_deg": hdg_diff, "implied_glide_angle_deg": glide,
            "ground_speed_mps": speed, "ground_speed_kt": speed_kt,
            "observed_latitude": state.get("latitude"), "observed_longitude": state.get("longitude"),
            "position_timestamp": state.get("position_timestamp"),
            "analysis_x_m": x, "analysis_y_m": y,
            "eta_window_s": {"min": eta_min, "max": eta_max}, "samples_in_corridor": sample_count,
            "data_age_s": age, "confidence": confidence, "vertical_rate_fpm": vertical_fpm}


class ApproachTracker:
    """Clock-driven state tracker; state is isolated per collector/mode."""
    def __init__(self, settings: dict):
        self.settings = settings
        self.tracks: dict[tuple[str, str], dict[str, Any]] = {}
        self.completed: list[dict] = []

    @staticmethod
    def _new_track(now: float) -> dict[str, Any]:
        return {"state": "CANDIDATE", "first_seen_ts": now, "last_seen_ts": now,
                "samples": 0, "position_sample_keys": set(), "confirmed_since": now,
                "outcome": None}

    def update(self, states: list[dict], runways: list, now: float) -> list[dict]:
        observed: set[tuple[str, str]] = set()
        current = []
        for state in states:
            for runway in runways:
                for end in (runway.end_a, runway.end_b):
                    key = (state["icao24"], end.identifier)
                    evidence = evaluate_approach(state, end, self.settings, now)
                    track = self.tracks.get(key)
                    if evidence.get("eligible"):
                        observed.add(key)
                        terminal = {"PASSED_THRESHOLD_ZONE", "LOST", "GO_AROUND_SUSPECTED", "LEFT_CORRIDOR"}
                        restart_after = float(self.settings.get("restart_after_s", self.settings["lost_timeout_s"]))
                        if track is None or (track["state"] in terminal and
                                             now - track["last_seen_ts"] >= restart_after):
                            track = self._new_track(now)
                            self.tracks[key] = track
                        track["last_seen_ts"] = now
                        track["not_eligible_since"] = None
                        position_sample_key = state.get("position_timestamp")
                        if position_sample_key is None:
                            position_sample_key = state.get("last_position_seen", now)
                        track["position_sample_keys"].add(position_sample_key)
                        track["samples"] = len(track["position_sample_keys"])
                        track["last_distance_m"] = evidence.get("distance_to_threshold_m")
                        if now - track["confirmed_since"] >= float(self.settings["confirm_s"]):
                            track["state"] = "NEAR_THRESHOLD" if evidence["distance_to_threshold_m"] <= float(self.settings["near_threshold_distance_m"]) else "LIKELY_APPROACHING"
                        evidence.update({"state": track["state"], "first_seen_ts": track["first_seen_ts"],
                                         "last_seen_ts": track["last_seen_ts"], "samples": track["samples"],
                                         "outcome": track.get("outcome")})
                        evidence["reasons"] = ["inside runway-end approach corridor", "track and movement consistent with approach"]
                        track["last_evidence"] = evidence
                        current.append(evidence)
                    elif track is not None and track["state"] in {"CANDIDATE", "LIKELY_APPROACHING", "NEAR_THRESHOLD"}:
                        track["not_eligible_since"] = track.get("not_eligible_since") or now
                        vertical = state.get("vertical_rate_mps") or 0.0
                        old = track.get("last_distance_m", evidence.get("distance_to_threshold_m", math.inf))
                        if vertical * 60 * M_TO_FT >= float(self.settings["go_around_climb_fpm"]) and evidence.get("distance_to_threshold_m", 0) > old:
                            track["state"], track["outcome"] = "GO_AROUND_SUSPECTED", "go_around_suspected"
                            track["not_eligible_since"] = None
                        elif evidence.get("along_track_distance_m", 1) <= 0:
                            track["state"], track["outcome"] = "PASSED_THRESHOLD_ZONE", "reached_threshold_zone"
                            track["not_eligible_since"] = None
                        if track["state"] in {"GO_AROUND_SUSPECTED", "PASSED_THRESHOLD_ZONE"}:
                            retained = dict(track.get("last_evidence") or evidence)
                            retained.update({"state":track["state"],"outcome":track["outcome"],"last_seen_ts":track["last_seen_ts"],
                                             "reasons":["climbing and receding after approach evidence" if track["state"]=="GO_AROUND_SUSPECTED" else "passed the runway threshold zone"]})
                            track["last_evidence"] = retained
                            current.append(retained)
                            continue
                        if now - track["not_eligible_since"] < float(self.settings["clear_hysteresis_s"]):
                            if track.get("last_evidence"):
                                current.append(dict(track["last_evidence"]))
                            continue
                        if not evidence.get("checks", {}).get("inside_corridor", False):
                            track["state"], track["outcome"] = "LEFT_CORRIDOR", "left_corridor"
                        track["last_distance_m"] = evidence.get("distance_to_threshold_m")
                        if track.get("last_evidence"):
                            retained = dict(track["last_evidence"])
                            retained.update({"state": track["state"], "outcome": track.get("outcome"),
                                "reasons": ["approach state retained through configured hysteresis window"], "last_seen_ts": track["last_seen_ts"]})
                            track["last_evidence"] = retained
                            current.append(retained)
        for key, track in list(self.tracks.items()):
            if key in observed or track["state"] in {"PASSED_THRESHOLD_ZONE", "LOST", "GO_AROUND_SUSPECTED", "LEFT_CORRIDOR"}:
                continue
            if now - track["last_seen_ts"] >= float(self.settings["lost_timeout_s"]):
                track["state"], track["outcome"] = "LOST", "lost_in_corridor"
                retained = dict(track.get("last_evidence") or {})
                retained.update({"state":"LOST", "outcome":"lost_in_corridor", "last_seen_ts":track["last_seen_ts"],
                                 "reasons":["aircraft observations lost after corridor approach evidence"]})
                track["last_evidence"] = retained
                if retained: current.append(retained)
        return current
