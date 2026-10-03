"""Clock-driven candidate/active/escalated/resolved alert lifecycle."""
from __future__ import annotations

import copy
import hashlib
from typing import Mapping, Sequence

from .risk import LEVELS, evaluate_pair_risk

_RANK = {level: index for index, level in enumerate(LEVELS)}


def _canonical_pair(pair: Mapping) -> dict:
    result = copy.deepcopy(dict(pair))
    a, b = result["aircraft_a"], result["aircraft_b"]
    if str(a["icao24"]).lower() > str(b["icao24"]).lower():
        result["aircraft_a"], result["aircraft_b"] = b, a
        result["data_age_a_s"], result["data_age_b_s"] = result.get("data_age_b_s"), result.get("data_age_a_s")
        positions = result.get("cpa_position", {})
        if "aircraft_a" in positions and "aircraft_b" in positions:
            positions["aircraft_a"], positions["aircraft_b"] = positions["aircraft_b"], positions["aircraft_a"]
    a_id, b_id = result["aircraft_a"]["icao24"].lower(), result["aircraft_b"]["icao24"].lower()
    result["pair_key"] = f"{a_id}|{b_id}"
    return result


class AlertManager:
    def __init__(self, *, settings: Mapping, airport: str, mode: str, risk_profile: str,
                 restored_events: Sequence[Mapping] = (), event_type: str = "AIR_CONFLICT"):
        self.settings = settings
        self.airport, self.mode, self.risk_profile = airport, mode, risk_profile
        self.event_type = event_type
        self.open_events: dict[tuple[str, str], dict] = {}
        self.last_events: dict[tuple[str, str], dict] = {}
        for event in restored_events:
            key = (event["event_type"], event["pair_key"])
            if event["status"] == "RESOLVED":
                self.last_events[key] = copy.deepcopy(dict(event))
            else:
                self.open_events[key] = copy.deepcopy(dict(event))

    def _event_id(self, pair_key: str, first_seen: float) -> str:
        stable = f"{self.mode}|{self.risk_profile}|{self.event_type}|{pair_key}|{first_seen:.6f}"
        return hashlib.sha256(stable.encode()).hexdigest()[:32]

    @staticmethod
    def _snapshot(states: Mapping[str, Mapping], pair: Mapping) -> dict:
        result = {}
        for suffix, aircraft in (("a", pair["aircraft_a"]), ("b", pair["aircraft_b"])):
            state = states.get(aircraft["icao24"], {})
            result[suffix] = {"icao24": aircraft["icao24"], "callsign": aircraft.get("callsign"),
                              "latitude": state.get("latitude"), "longitude": state.get("longitude"),
                              "x_m": state.get("x_m"), "y_m": state.get("y_m"),
                              "analysis_x_m": state.get("analysis_x_m"), "analysis_y_m": state.get("analysis_y_m"),
                              "baro_altitude_m": state.get("baro_altitude_m"),
                              "geo_altitude_m": state.get("geo_altitude_m"),
                              "velocity_mps": state.get("velocity_mps"), "track_deg": state.get("track_deg"),
                              "vx_mps": state.get("vx_mps"), "vy_mps": state.get("vy_mps"),
                              "vertical_rate_mps": state.get("vertical_rate_mps"),
                              "age_s": state.get("age_s"), "quality_flags": list(state.get("quality_flags", ())),
                              "on_ground": state.get("on_ground")}
        return result

    def _new_event(self, pair: Mapping, now: float, risk: Mapping, states: Mapping) -> dict:
        previous = self.last_events.get((self.event_type, pair["pair_key"]))
        cooldown_s = float(self.settings["cooldown_s"])
        continuation = bool(previous and now - float(previous.get("resolved_ts") or 0) <= cooldown_s)
        event_id = previous["event_id"] if continuation else self._event_id(pair["pair_key"], now)
        continuation_count = int(previous.get("continuation_count", 0)) + 1 if continuation else 0
        reasons = list(risk["reasons"])
        alert_kind = "WATCH" if risk["level"] == "LOW" and _RANK[self.settings["min_alert_level"]] > _RANK["LOW"] else "ALERT"
        if alert_kind == "WATCH":
            reasons.append("LOW-level condition is shown as a watch item by configuration.")
        if continuation:
            reasons.append(f"Continuation of event during the {cooldown_s:g} s cooldown.")
        states_snapshot = self._snapshot(states, pair)
        cpa_positions = pair.get("cpa_position", {})
        cpa_a, cpa_b = cpa_positions.get("aircraft_a", {}), cpa_positions.get("aircraft_b", {})
        event = {
            "event_id": event_id, "event_type": self.event_type, "status": "CANDIDATE",
            "mode": self.mode, "risk_profile": self.risk_profile, "airport": self.airport,
            "alert_kind": alert_kind,
            "aircraft_1": copy.deepcopy(pair["aircraft_a"]), "aircraft_2": copy.deepcopy(pair["aircraft_b"]),
            "pair_key": pair["pair_key"], "first_seen_ts": float(previous["first_seen_ts"]) if continuation else now,
            "last_updated_ts": now, "opened_ts": previous.get("opened_ts") if continuation else None,
            "resolved_ts": None, "resolution_reason": None,
            "current_risk": risk["level"], "peak_risk": risk["level"],
            "h_sep_cpa_m": pair["h_sep_cpa_m"], "min_h_sep_cpa_m": pair["h_sep_cpa_m"],
            "v_sep_cpa_m": pair.get("v_sep_cpa_m"), "min_v_sep_cpa_m": pair.get("v_sep_cpa_m"),
            "time_to_cpa_s": pair["t_cpa_s"], "min_time_to_cpa_s": pair["t_cpa_s"],
            "closing_speed_mps": pair.get("closing_speed_mps"), "altitude_basis": pair.get("altitude_basis"),
            "data_age_a_s": pair.get("data_age_a_s"), "data_age_b_s": pair.get("data_age_b_s"),
            "reasons": reasons, "confidence": risk.get("confidence", "LOW"),
            "aircraft_states_first": states_snapshot, "aircraft_states_peak": states_snapshot,
            "cpa_lat": pair.get("cpa_position", {}).get("midpoint", {}).get("latitude"),
            "cpa_lon": pair.get("cpa_position", {}).get("midpoint", {}).get("longitude"),
            "cpa_aircraft_a_lat": cpa_a.get("latitude"), "cpa_aircraft_a_lon": cpa_a.get("longitude"),
            "cpa_aircraft_b_lat": cpa_b.get("latitude"), "cpa_aircraft_b_lon": cpa_b.get("longitude"),
            "cycles_observed": int(previous.get("cycles_observed", 0)) if continuation else 0,
            "confirmation_elapsed_s": 0.0, "confirmation_started_ts": now, "confirmation_cycles": 0,
            "continuation_count": continuation_count, "duration_s": max(0.0, now - (float(previous["first_seen_ts"]) if continuation else now)),
            "last_seen_ts": now,
            "below_threshold_since_ts": None, "below_threshold_cycles": 0, "missing_since_ts": None,
            "score_components": risk.get("score_components", {}), "filtered_reason": risk.get("filtered_reason"),
        }
        if continuation:
            event["peak_risk"] = max((previous.get("peak_risk", "NORMAL"), risk["level"]), key=_RANK.__getitem__)
            event["min_h_sep_cpa_m"] = min(float(previous.get("min_h_sep_cpa_m", pair["h_sep_cpa_m"])),
                                             float(pair["h_sep_cpa_m"]))
            prior_v = previous.get("min_v_sep_cpa_m")
            if prior_v is not None and event["min_v_sep_cpa_m"] is not None:
                event["min_v_sep_cpa_m"] = min(float(prior_v), float(event["min_v_sep_cpa_m"]))
            event["min_time_to_cpa_s"] = min(float(previous.get("min_time_to_cpa_s", pair["t_cpa_s"])),
                                               float(pair["t_cpa_s"]))
            event["cycles_observed"] = int(previous.get("cycles_observed", 0))
            event["aircraft_states_first"] = copy.deepcopy(previous.get("aircraft_states_first"))
            event["aircraft_states_peak"] = copy.deepcopy(previous.get("aircraft_states_peak"))
        return event

    @staticmethod
    def _update_metrics(event: dict, pair: Mapping, risk: Mapping, states: Mapping) -> None:
        old_peak = event["peak_risk"]
        old_min_h = float(event["min_h_sep_cpa_m"])
        old_min_v = event["min_v_sep_cpa_m"]
        event["current_risk"] = risk["level"]
        event["h_sep_cpa_m"] = pair["h_sep_cpa_m"]
        event["min_h_sep_cpa_m"] = min(float(event["min_h_sep_cpa_m"]), float(pair["h_sep_cpa_m"]))
        event["v_sep_cpa_m"] = pair.get("v_sep_cpa_m")
        if pair.get("v_sep_cpa_m") is not None:
            event["min_v_sep_cpa_m"] = (float(pair["v_sep_cpa_m"]) if event["min_v_sep_cpa_m"] is None
                                         else min(float(event["min_v_sep_cpa_m"]), float(pair["v_sep_cpa_m"])))
        event["time_to_cpa_s"] = pair["t_cpa_s"]
        event["min_time_to_cpa_s"] = min(float(event["min_time_to_cpa_s"]), float(pair["t_cpa_s"]))
        event["closing_speed_mps"] = pair.get("closing_speed_mps")
        event["altitude_basis"] = pair.get("altitude_basis")
        event["data_age_a_s"], event["data_age_b_s"] = pair.get("data_age_a_s"), pair.get("data_age_b_s")
        event["confidence"] = risk.get("confidence", "LOW")
        event["score_components"] = risk.get("score_components", {})
        event["reasons"] = list(risk.get("reasons", []))
        if event.get("alert_kind") == "WATCH":
            event["reasons"].append("LOW-level condition is shown as a watch item by configuration.")
        if event.get("continuation_count") and event.get("confirmation_cycles") == 1:
            event["reasons"].append("Continuation of a resolved event during cooldown.")
        midpoint = pair.get("cpa_position", {}).get("midpoint", {})
        event["cpa_lat"], event["cpa_lon"] = midpoint.get("latitude"), midpoint.get("longitude")
        cpa_a = pair.get("cpa_position", {}).get("aircraft_a", {})
        cpa_b = pair.get("cpa_position", {}).get("aircraft_b", {})
        event["cpa_aircraft_a_lat"], event["cpa_aircraft_a_lon"] = cpa_a.get("latitude"), cpa_a.get("longitude")
        event["cpa_aircraft_b_lat"], event["cpa_aircraft_b_lon"] = cpa_b.get("latitude"), cpa_b.get("longitude")
        event["last_seen_ts"] = event["last_updated_ts"]
        event["missing_since_ts"] = None
        new_minimum = float(pair["h_sep_cpa_m"]) < old_min_h or (
            pair.get("v_sep_cpa_m") is not None and (old_min_v is None or
            float(pair["v_sep_cpa_m"]) < float(old_min_v)))
        if _RANK[risk["level"]] > _RANK[old_peak] or new_minimum:
            event["peak_risk"] = risk["level"]
            event["aircraft_states_peak"] = AlertManager._snapshot(states, pair)

    def process_cycle(self, pairs: Sequence[Mapping], states: Sequence[Mapping], now: float) -> list[dict]:
        """Advance all events for one pipeline cycle and return changed snapshots."""
        settings = self.settings
        state_map = {str(state["icao24"]).lower(): state for state in states}
        observed: dict[str, tuple[dict, dict]] = {}
        for raw_pair in pairs:
            pair = _canonical_pair(raw_pair)
            risk = evaluate_pair_risk(pair, settings, states_by_id=state_map,
                                      profile_name=self.risk_profile)
            observed[pair["pair_key"]] = pair, risk

        changed: dict[str, dict] = {}
        minimum_level = settings["min_alert_level"]
        threshold_rank = _RANK[minimum_level]
        low_watch = bool(settings.get("allow_low_watch", False))
        for pair_key, (pair, risk) in observed.items():
            key = (self.event_type, pair_key)
            is_eligible = risk["filtered_reason"] is None and (
                _RANK[risk["level"]] >= threshold_rank or (low_watch and risk["level"] == "LOW"))
            event = self.open_events.get(key)
            if not is_eligible:
                if event:
                    event["last_updated_ts"] = now
                    event["duration_s"] = max(0.0, now - float(event["first_seen_ts"]))
                    self._update_metrics(event, pair, risk, state_map)
                    event["last_updated_ts"] = now
                    self._mark_below(event, now)
                    if self._should_clear(event, now):
                        self._resolve(key, event, now, "threshold_cleared", changed)
                    else:
                        changed[event["event_id"]] = {"sub_event": "updated", "event": copy.deepcopy(event)}
                continue

            if event is None:
                event = self._new_event(pair, now, risk, state_map)
                self.open_events[key] = event
            event["cycles_observed"] += 1
            event["confirmation_elapsed_s"] = max(0.0, now - float(event["confirmation_started_ts"]))
            event["confirmation_cycles"] += 1
            event["last_updated_ts"] = now
            event["duration_s"] = max(0.0, now - float(event["first_seen_ts"]))
            event["last_seen_ts"] = now
            event["below_threshold_since_ts"] = None
            event["below_threshold_cycles"] = 0
            prior_current = event["current_risk"]
            self._update_metrics(event, pair, risk, state_map)
            event["last_updated_ts"] = now
            event["duration_s"] = max(0.0, now - float(event["first_seen_ts"]))
            if event["status"] == "CANDIDATE":
                confirmation = settings["confirmation"]
                if (event["confirmation_cycles"] >= int(confirmation["confirm_cycles"]) and
                        event["confirmation_elapsed_s"] >= float(confirmation["confirm_min_seconds"])):
                    event["status"] = "ACTIVE"
                    event["opened_ts"] = now
                    event["reasons"] = list(risk["reasons"])
                    event["reasons"].append(f"Confirmed over {event['confirmation_elapsed_s']:.1f} s across {event['confirmation_cycles']} cycles.")
                    changed[event["event_id"]] = {"sub_event": "opened", "event": copy.deepcopy(event)}
                    continue
            elif _RANK[risk["level"]] > _RANK[prior_current]:
                event["status"] = "ESCALATED"
                changed[event["event_id"]] = {"sub_event": "escalated", "event": copy.deepcopy(event)}
                continue
            if event["status"] in ("ACTIVE", "ESCALATED"):
                changed[event["event_id"]] = {"sub_event": "updated", "event": copy.deepcopy(event)}
            else:
                changed[event["event_id"]] = copy.deepcopy(event)

        for key, event in list(self.open_events.items()):
            if key[1] in observed:
                continue
            ids = (event["aircraft_1"]["icao24"].lower(), event["aircraft_2"]["icao24"].lower())
            pair_states = [state_map.get(icao) for icao in ids]
            stale_flags = {"LOW_QUALITY", "stale_position", "position_missing"}
            tracks_available = all(state is not None and state.get("latitude") is not None and
                                   state.get("longitude") is not None and
                                   float(state.get("age_s") or 0) <= float(settings["max_data_age_for_alert_s"]) and
                                   not (set(state.get("quality_flags", ())) & stale_flags)
                                   for state in pair_states)
            if tracks_available:
                event["last_updated_ts"] = now
                event["duration_s"] = max(0.0, now - float(event["first_seen_ts"]))
                event["current_risk"] = "NORMAL"
                event["reasons"] = ["Pair no longer meets the configured CPA pre-filter."]
                event["missing_since_ts"] = None
                self._mark_below(event, now)
                if self._should_clear(event, now):
                    self._resolve(key, event, now, "threshold_cleared", changed)
                else:
                    changed[event["event_id"]] = {"sub_event": "updated", "event": copy.deepcopy(event)}
                continue
            if event["missing_since_ts"] is None:
                event["missing_since_ts"] = now
            event["last_updated_ts"] = now
            event["duration_s"] = max(0.0, now - float(event["first_seen_ts"]))
            if now - float(event["missing_since_ts"]) >= float(settings["data_lost_timeout_s"]):
                self._resolve(key, event, now, "data_lost", changed)
            else:
                changed[event["event_id"]] = copy.deepcopy(event)
        return list(changed.values())

    def _mark_below(self, event: dict, now: float) -> None:
        if event["below_threshold_since_ts"] is None:
            event["below_threshold_since_ts"] = now
            event["below_threshold_cycles"] = 1
        else:
            event["below_threshold_cycles"] += 1

    def _should_clear(self, event: Mapping, now: float) -> bool:
        if _RANK[event["current_risk"]] >= _RANK[self.settings["clear"]["clear_below_level"]]:
            return False
        clear = self.settings["clear"]
        elapsed = now - float(event["below_threshold_since_ts"])
        return (int(event["below_threshold_cycles"]) >= int(clear["clear_cycles"]) and
                elapsed >= float(clear["clear_min_seconds"]))

    def _resolve(self, key: tuple[str, str], event: dict, now: float,
                 resolution_reason: str, changed: dict[str, dict]) -> None:
        event["status"] = "RESOLVED"
        event["resolved_ts"] = now
        event["last_updated_ts"] = now
        event["duration_s"] = max(0.0, now - float(event["first_seen_ts"]))
        event["resolution_reason"] = resolution_reason
        self.open_events.pop(key, None)
        self.last_events[key] = event
        changed[event["event_id"]] = {"sub_event": "resolved", "event": copy.deepcopy(event)}

    def finalize(self, now: float, resolution_reason: str) -> list[dict]:
        """Close open records at a replay boundary without implying a safe outcome."""
        changed: dict[str, dict] = {}
        for key, event in list(self.open_events.items()):
            self._resolve(key, event, now, resolution_reason, changed)
        return list(changed.values())
