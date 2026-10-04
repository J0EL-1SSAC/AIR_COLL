"""Conservative inferred airport activity; inferred events are never observations."""
from __future__ import annotations

import math

M_TO_FT=3.280839895
M_TO_NM=1/1852.0


def infer_landing(approach: dict, settings: dict, *, airport_elevation_m: float) -> dict | None:
    if approach.get("outcome") not in {"lost_in_corridor", "reached_threshold_zone"}:
        return None
    if approach.get("outcome")=="go_around_suspected" or approach.get("state")=="GO_AROUND_SUSPECTED":
        return None
    samples=int(approach.get("samples") or approach.get("samples_in_corridor") or 0)
    altitude=approach.get("altitude_m")
    distance=approach.get("distance_to_threshold_nm")
    reasons=[]
    if samples<int(settings["min_samples"]): reasons.append("too few distinct position samples")
    if distance is None or float(distance)>float(settings["landing_inference_max_distance_nm"]): reasons.append("last observed position is beyond landing distance limit")
    height_ft=None if altitude is None else (float(altitude)-airport_elevation_m)*M_TO_FT
    if height_ft is None or height_ft>float(settings["landing_inference_max_height_ft"]): reasons.append("last observed height is above landing inference limit")
    if approach.get("heading_difference_deg") is None or float(approach["heading_difference_deg"])>float(settings["max_heading_diff_deg"]): reasons.append("track is not aligned with runway true heading")
    if approach.get("vertical_rate_fpm") is None or float(approach["vertical_rate_fpm"])>=0: reasons.append("descending profile not observed")
    if reasons: return None
    confidence="HIGH" if samples>=int(settings["high_confidence_min_samples"]) else "MEDIUM" if samples>=int(settings["medium_confidence_min_samples"]) else "LOW"
    return {"activity_type":"LIKELY_LANDED","icao24":approach["icao24"],"callsign":approach.get("callsign"),
        "runway_end":approach.get("runway_end") or "ambiguous","time_ts":float(approach.get("last_seen_ts",0)),
        "latitude":approach.get("observed_latitude"),"longitude":approach.get("observed_longitude"),
        "height_above_airport_ft":height_ft,"distance_to_threshold_nm":float(distance),"samples":samples,
        "confidence":confidence,"inferred":True,"evidence":["approach track ended near a runway threshold", "last observed altitude and descent met configured limits", "track aligned with the runway true heading"]}


def infer_departure(state: dict, runway_ends: list, settings: dict, *, airport_elevation_m: float) -> dict | None:
    history=[point for point in state.get("history",[]) if point.get("x_m") is not None and point.get("y_m") is not None]
    if len(history)<int(settings["departure_min_samples"]): return None
    if state.get("track_deg") is None or state.get("vertical_rate_mps") is None: return None
    if float(state["vertical_rate_mps"])*60*M_TO_FT<float(settings["departure_min_climb_fpm"]): return None
    altitude=state.get("geo_altitude_m") if state.get("geo_altitude_m") is not None else state.get("baro_altitude_m")
    if altitude is None or (float(altitude)-airport_elevation_m)*M_TO_FT>float(settings["departure_max_height_ft"]): return None
    candidates=[]
    for end in runway_ends:
        distance=math.hypot(float(state["x_m"])-float(end.x_m),float(state["y_m"])-float(end.y_m))*M_TO_NM
        difference=abs((float(state["track_deg"])-float(end.true_heading_deg)+180)%360-180)
        if distance<=float(settings["departure_max_distance_nm"]) and difference<=float(settings["max_heading_diff_deg"]):
            candidates.append((distance,end))
    if not candidates: return None
    candidates.sort(key=lambda row:row[0])
    end=candidates[0][1]
    return {"activity_type":"LIKELY_DEPARTED","icao24":state["icao24"],"callsign":state.get("callsign"),
        "runway_end":end.identifier,"time_ts":float(state.get("first_seen",0)),"latitude":history[0].get("latitude"),
        "longitude":history[0].get("longitude"),"height_above_airport_ft":(float(altitude)-airport_elevation_m)*M_TO_FT,
        "distance_to_threshold_nm":candidates[0][0],"samples":len(history),"confidence":"MEDIUM",
        "inferred":True,"evidence":["first recorded track samples were near a runway end", "aircraft aligned with runway true heading", "climb was observed"]}
