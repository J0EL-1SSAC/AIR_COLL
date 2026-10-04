"""Experimental runway occupancy observations; never infer a runway is clear."""
from __future__ import annotations

from .geometry import heading_difference, point_in_buffer


class OccupancyTracker:
    def __init__(self, settings: dict):
        self.settings = settings
        self.samples: dict[tuple[str, str], list[tuple[float, dict]]] = {}
        self.ghosts: dict[str, dict] = {}

    def update(self, states: list[dict], runways: list, now: float, assessable: bool, gate: dict) -> dict:
        runway_status = {}
        for runway in runways:
            runway_status[runway.identifier] = {"runway": runway.identifier, "status": "UNKNOWN",
                "confidence": "LOW", "occupancy_assessable": bool(assessable), "coverage_gate": gate,
                "experimental": True, "aircraft": [],
                "reason": "Runway occupancy not assessable with current data" if not assessable else "No persistent runway activity has met the experimental evidence rules; this is not a clearance."}
        if not assessable or not self.settings.get("enabled", True):
            return {"runways": runway_status, "ghosts": list(self.ghosts.values()), "experimental": True}
        active_ids = {state["icao24"] for state in states
                      if float(state.get("age_s") or 0) <= float(self.settings["max_data_age_s"])
                      and (state.get("analysis_x_m") is not None or state.get("x_m") is not None)
                      and (state.get("analysis_y_m") is not None or state.get("y_m") is not None)}
        for state in states:
            x = state.get("analysis_x_m") if state.get("analysis_x_m") is not None else state.get("x_m")
            y = state.get("analysis_y_m") if state.get("analysis_y_m") is not None else state.get("y_m")
            if x is None or y is None or float(state.get("age_s") or 0) > float(self.settings["max_data_age_s"]):
                continue
            for runway in runways:
                if not point_in_buffer(x, y, runway.buffer_polygon):
                    continue
                key = (state["icao24"], runway.identifier)
                history = self.samples.setdefault(key, [])
                history.append((now, state))
                history[:] = [(ts, row) for ts, row in history if now-ts <= float(self.settings["min_persistence_s"])]
                min_samples = int(self.settings["min_consistent_samples"])
                duration = history[-1][0] - history[0][0] if len(history) > 1 else 0.0
                altitude = state.get("geo_altitude_m") if state.get("geo_altitude_m") is not None else state.get("baro_altitude_m")
                low = altitude is not None and abs(altitude - float(self.settings.get("ground_elevation_m", 0.0))) <= float(self.settings["ground_altitude_tolerance_m"])
                aligned = state.get("track_deg") is not None and heading_difference(state["track_deg"], runway.end_a.true_heading_deg) <= float(self.settings["alignment_tolerance_deg"])
                perpendicular = state.get("track_deg") is not None and min(heading_difference(state["track_deg"], runway.end_a.true_heading_deg), heading_difference(state["track_deg"], runway.end_b.true_heading_deg)) >= float(self.settings["crossing_min_angle_deg"])
                ground_flag = state.get("on_ground") is True
                enough = len(history) >= min_samples and duration >= float(self.settings["min_persistence_s"])
                if not enough:
                    status = "AIRCRAFT_NEAR_RUNWAY"
                elif ground_flag and aligned and (state.get("velocity_mps") or 0) <= float(self.settings["max_ground_speed_mps"]):
                    status = "LIKELY_ON_RUNWAY_ALIGNED"
                elif perpendicular:
                    status = "LIKELY_CROSSING"
                elif ground_flag and aligned:
                    status = "LIKELY_LANDING_ROLLOUT" if low else "LIKELY_TAKEOFF_ROLL"
                else:
                    status = "AIRCRAFT_NEAR_RUNWAY"
                confidence = "HIGH" if enough and ground_flag and low else "MEDIUM" if enough else "LOW"
                observation = {"icao24": state["icao24"], "callsign": state.get("callsign"), "status": status,
                    "confidence": confidence, "samples": len(history), "persistence_s": duration,
                    "single_sample_or_extrapolation": len(history) < min_samples,
                    "on_ground_reported": ground_flag, "altitude_m": altitude, "speed_mps": state.get("velocity_mps"),
                    "reason": "Experimental classification from buffered position, reported altitude/ground state, speed, heading and persistence."}
                runway_status[runway.identifier]["aircraft"].append(observation)
                runway_status[runway.identifier]["status"] = status
                runway_status[runway.identifier]["confidence"] = confidence
                runway_status[runway.identifier]["reason"] = observation["reason"]
                self.ghosts[state["icao24"]] = {**observation, "runway": runway.identifier, "last_seen_ts": now,
                                                  "expires_at": now + float(self.settings["ghost_ttl_s"])}
        for icao, ghost in list(self.ghosts.items()):
            if icao not in active_ids and now <= ghost["expires_at"]:
                ghost["status"] = "LOST_NEAR_RUNWAY"
                ghost["confidence"] = "LOW"
                runway_status[ghost["runway"]]["status"] = "LOST_NEAR_RUNWAY"
                runway_status[ghost["runway"]]["confidence"] = "LOW"
                runway_status[ghost["runway"]]["reason"] = "Last reported near the runway; data was lost. This is not a clearance."
                runway_status[ghost["runway"]]["aircraft"].append(ghost)
            elif now > ghost["expires_at"]:
                del self.ghosts[icao]
        for item in runway_status.values():
            if not item["aircraft"] and item["status"] == "UNKNOWN":
                item["status"] = "NO_REPORTED_ACTIVITY"
                item["confidence"] = "LOW"
                item["reason"] = "No aircraft reports matched the configured runway evidence. This does not establish that the runway is clear."
        return {"runways": runway_status, "ghosts": list(self.ghosts.values()), "experimental": True}
