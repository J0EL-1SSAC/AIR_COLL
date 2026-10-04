#!/usr/bin/env python3
"""Replay recorded source cycles through the state and approach trackers; never generates data."""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import statistics

import yaml
from pyproj import Transformer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.airwatch.approach import ApproachTracker, evaluate_approach
from backend.airwatch.airport_activity import infer_departure, infer_landing
from backend.airwatch.occupancy import OccupancyTracker
from backend.airwatch.coverage_analysis import analyze_coverage
from backend.airwatch.clock import ReplayClock
from backend.airwatch.models import AirportCenter
from backend.airwatch.replay import ReplaySource
from backend.airwatch.runways import load_runways
from backend.airwatch.state_manager import AircraftStateManager

def parse_time(value):
    if value is None: return None
    return datetime.fromisoformat(value.replace("Z","+00:00")).astimezone(timezone.utc).timestamp()

async def run(args):
    config=yaml.safe_load((ROOT/"config.yaml").read_text())
    db_path=Path(args.db); db_path=db_path if db_path.is_absolute() else ROOT/db_path
    start,end=parse_time(args.start),parse_time(args.end)
    source=ReplaySource(db_path,start_time=start,end_time=end)
    airport=config["airport"]
    center=AirportCenter(airport["icao"],float(airport["latitude"]),float(airport["longitude"]))
    local=f"+proj=aeqd +lat_0={center.latitude} +lon_0={center.longitude} +datum=WGS84 +units=m +no_defs"
    transform=Transformer.from_crs("EPSG:4326",local,always_xy=True)
    manager=AircraftStateManager(latitude=center.latitude,longitude=center.longitude,
        history_length=int(config["state_manager"]["history_length"]),
        low_quality_after_s=float(config["state_manager"]["low_quality_after_s"]),
        drop_after_s=float(config["state_manager"]["drop_after_s"]),
        tombstone_s=float(config["state_manager"]["tombstone_s"]),
        low_altitude_ft=float(config["collector"]["low_altitude_ft"]),
        smoothing_enabled=bool(config["state_manager"]["smoothing_enabled"]),
        alpha=float(config["state_manager"]["alpha"]),beta=float(config["state_manager"]["beta"]),transform=transform)
    runway_settings=dict(config["runways"])
    runway_settings["data_file"]=ROOT/runway_settings["data_file"]
    runway_settings["override_file"]=ROOT/runway_settings["override_file"]
    try:
        runways=load_runways(airport_ident=center.icao,latitude=center.latitude,longitude=center.longitude,settings=runway_settings)
        runway_error=None
    except Exception as error:
        runways=[]; runway_error=str(error)
    tracker=ApproachTracker(config["approach"])
    occupancy_tracker=OccupancyTracker(config["occupancy"])
    measured=analyze_coverage(db_path=db_path,airport_latitude=center.latitude,airport_longitude=center.longitude,
        airport_elevation_m=float(config["coverage_report"]["ground_elevation_m"]),radius_nm=float(airport["radius_nm"]),
        settings=config["coverage_report"],runways=runways,start=start,end=end)
    gate_numbers={"recording_hours":measured["duration_hours"],
        "low_altitude_reports_per_hour_inside_buffer":measured.get("runway_buffer_low_altitude_reports_per_hour"),
        "on_ground_reports_per_hour_inside_buffer":measured.get("runway_buffer_on_ground_reports_per_hour"),
        "runway_buffer_reports_per_hour":measured.get("runway_buffer_reports_per_hour")}
    thresholds={"recording_hours":float(config["occupancy"]["minimum_recording_hours"]),
        "low_altitude_reports_per_hour_inside_buffer":float(config["occupancy"]["minimum_low_altitude_buffer_reports_per_hour"]),
        "on_ground_reports_per_hour_inside_buffer":float(config["occupancy"]["minimum_on_ground_buffer_reports_per_hour"])}
    occupancy_assessable=bool(runways and gate_numbers["recording_hours"]>=thresholds["recording_hours"] and
        gate_numbers["low_altitude_reports_per_hour_inside_buffer"] is not None and
        gate_numbers["on_ground_reports_per_hour_inside_buffer"] is not None and
        gate_numbers["runway_buffer_reports_per_hour"] is not None and
        gate_numbers["low_altitude_reports_per_hour_inside_buffer"]>=thresholds["low_altitude_reports_per_hour_inside_buffer"] and
        gate_numbers["on_ground_reports_per_hour_inside_buffer"]>=thresholds["on_ground_reports_per_hour_inside_buffer"] and
        gate_numbers["runway_buffer_reports_per_hour"]>=thresholds["on_ground_reports_per_hour_inside_buffer"])
    gate={"assessable":occupancy_assessable,"numbers":gate_numbers,"thresholds":thresholds,
        "reason":"Measured runway-buffer coverage meets configured criteria." if occupancy_assessable else
        "Runway occupancy not assessable with current data: runway-buffer coverage metrics or geometry are insufficient."}
    clock=ReplayClock(); end_counts=Counter(); hourly=Counter(); sample_counts=defaultdict(int); track_summaries={}; too_few=set(); occupancy=Counter(); low_samples=0
    position_samples=defaultdict(set)
    counted_tracks=set(); corridor_segments={}; insufficient_corridor_segments=set()
    inferred_activities={"LIKELY_LANDED":set(),"LIKELY_DEPARTED":set()}
    first=last=None
    for _ in range(source.total_cycles):
        timestamp=source.next_fetch_time
        if timestamp is None: break
        clock.set(timestamp); first=timestamp if first is None else first; last=timestamp
        states=await source.fetch_states(center,float(airport["radius_nm"]))
        manager.update(states,timestamp)
        snapshot=manager.snapshot(timestamp,include_unpositioned=True)
        corridor_present=set()
        for state in snapshot:
            for runway in runways:
                for runway_end in (runway.end_a,runway.end_b):
                    evidence=evaluate_approach(state,runway_end,config["approach"],timestamp)
                    if (evidence.get("checks",{}).get("inside_corridor")
                            and evidence.get("checks",{}).get("fresh_data")):
                        corridor_present.add((state["icao24"],runway_end.identifier))
        segment_gap=float(config["coverage_report"]["max_track_gap_s"])
        for key,segment in list(corridor_segments.items()):
            if timestamp-segment["last"] > segment_gap:
                if segment["samples"] < int(config["approach"]["min_closing_samples"]):
                    insufficient_corridor_segments.add((key,segment["first"]))
                del corridor_segments[key]
        for key in corridor_present:
            state=next((item for item in snapshot if item["icao24"]==key[0]),None)
            sample_ts=timestamp if state is None or state.get("position_timestamp") is None else float(state["position_timestamp"])
            segment=corridor_segments.get(key)
            if segment is None:
                corridor_segments[key]={"first":timestamp,"last":timestamp,"samples":1,"last_sample_ts":sample_ts}
            elif segment["last_sample_ts"] != sample_ts:
                segment["last"]=timestamp; segment["samples"]+=1; segment["last_sample_ts"]=sample_ts
        approaches=tracker.update(snapshot,runways,timestamp)
        for item in approaches:
            key=item["runway_end"]
            approach_key=(item["icao24"],key,item.get("first_seen_ts"))
            sample_counts[approach_key]=max(sample_counts[approach_key],int(item.get("samples",0)))
            state=next((value for value in snapshot if value["icao24"]==item["icao24"]),None)
            sample_time=(float(state["position_timestamp"]) if state and state.get("position_timestamp") is not None else timestamp)
            position_samples[approach_key].add(sample_time)
            altitude_m=item.get("altitude_m")
            previous=track_summaries.get(approach_key)
            minimum_altitude=min([v for v in ((previous or {}).get("minimum_altitude_m"),altitude_m) if v is not None],default=None)
            track_summaries[approach_key]={"tracker_samples":int(item.get("samples",0)),
                "callsign":item.get("callsign"),
                "first_seen_ts":float(item.get("first_seen_ts",timestamp)),
                "last_distance_to_threshold_m":item.get("distance_to_threshold_m"),
                "first_distance_to_threshold_m":((previous or {}).get("first_distance_to_threshold_m")
                    if previous else item.get("distance_to_threshold_m")),
                "last_seen_ts":item.get("last_seen_ts"),"first_position_ts":min(position_samples[approach_key]),
                "last_position_ts":max(position_samples[approach_key]),"outcome":item.get("outcome"),
                "minimum_altitude_m":minimum_altitude,"last_altitude_m":item.get("altitude_m"),
                "last_along_track_distance_m":item.get("along_track_distance_m"),
                "last_cross_track_error_m":item.get("cross_track_error_m"),
                "last_implied_glide_angle_deg":item.get("implied_glide_angle_deg"),
                "last_heading_difference_deg":item.get("heading_difference_deg"),
                "last_vertical_rate_fpm":item.get("vertical_rate_fpm"),
                "last_analysis_x_m":item.get("analysis_x_m"),"last_analysis_y_m":item.get("analysis_y_m"),
                "last_latitude":item.get("observed_latitude"),"last_longitude":item.get("observed_longitude"),
                "confidence":item.get("confidence","LOW")}
            if len(position_samples[approach_key])<2: too_few.add(approach_key)
            inferred=infer_landing(item,config["airport_activity"],airport_elevation_m=float(config["coverage_report"]["ground_elevation_m"]))
            if inferred:
                inferred_activities["LIKELY_LANDED"].add((inferred["icao24"],inferred["runway_end"],inferred["time_ts"]))
            if approach_key not in counted_tracks:
                counted_tracks.add(approach_key)
                hour=datetime.fromtimestamp(timestamp,timezone.utc).strftime("%Y-%m-%d %H:00Z")
                hourly[(hour,key)]+=1
        low_samples += sum(1 for state in snapshot if (state.get("geo_altitude_m") if state.get("geo_altitude_m") is not None else state.get("baro_altitude_m")) is not None and
            (state.get("geo_altitude_m") if state.get("geo_altitude_m") is not None else state.get("baro_altitude_m")) - float(config["coverage_report"]["ground_elevation_m"]) <= float(config["coverage_report"]["approach_altitude_ceiling_m"]))
        runway_ends=[end for runway in runways for end in (runway.end_a,runway.end_b)]
        for state in snapshot:
            inferred=infer_departure(state,runway_ends,config["airport_activity"],airport_elevation_m=float(config["coverage_report"]["ground_elevation_m"]))
            if inferred: inferred_activities["LIKELY_DEPARTED"].add((inferred["icao24"],inferred["runway_end"],inferred["time_ts"]))
        occupancy_data=occupancy_tracker.update(snapshot,runways,timestamp,occupancy_assessable,gate)
        for runway_state in occupancy_data["runways"].values(): occupancy[runway_state["status"]]+=1
    for key,segment in corridor_segments.items():
        if segment["samples"] < int(config["approach"]["min_closing_samples"]):
            insufficient_corridor_segments.add((key,segment["first"]))
    too_few={key for key,samples in position_samples.items() if len(samples)<2}
    diagnostics=[]
    for (icao24,runway_end,first_seen),summary in track_summaries.items():
        distinct=len(position_samples[(icao24,runway_end,first_seen)])
        first_pos=summary.get("first_position_ts")
        last_pos=summary.get("last_position_ts")
        diagnostics.append({"icao24":icao24,"callsign":summary.get("callsign"),"runway_end":runway_end,
            "first_seen_ts":first_seen,"last_seen_ts":summary.get("last_seen_ts"),
            "distinct_position_samples":distinct,"tracker_poll_samples":summary.get("tracker_samples",0),
            "position_time_span_s":None if first_pos is None or last_pos is None else last_pos-first_pos,
            "first_distance_to_threshold_nm":None if summary.get("first_distance_to_threshold_m") is None else summary["first_distance_to_threshold_m"]/1852.0,
            "last_distance_to_threshold_nm":None if summary.get("last_distance_to_threshold_m") is None else summary["last_distance_to_threshold_m"]/1852.0,
            "last_altitude_m":summary.get("last_altitude_m"),"last_along_track_distance_m":summary.get("last_along_track_distance_m"),
            "last_cross_track_error_m":summary.get("last_cross_track_error_m"),
            "last_implied_glide_angle_deg":summary.get("last_implied_glide_angle_deg"),
            "last_heading_difference_deg":summary.get("last_heading_difference_deg"),
            "last_vertical_rate_fpm":summary.get("last_vertical_rate_fpm"),
            "last_analysis_x_m":summary.get("last_analysis_x_m"),"last_analysis_y_m":summary.get("last_analysis_y_m"),
            "last_latitude":summary.get("last_latitude"),"last_longitude":summary.get("last_longitude"),
            "outcome":summary.get("outcome"),"confidence":summary.get("confidence","LOW")})
    sample_distribution=[item["distinct_position_samples"] for item in diagnostics]
    span_distribution=[item["position_time_span_s"] for item in diagnostics if item["position_time_span_s"] is not None]
    distribution=lambda values: None if not values else {"min":min(values),"median":statistics.median(values),"max":max(values)}
    out={"cycles":source.total_cycles,"recording_start_utc":None if first is None else datetime.fromtimestamp(first,timezone.utc).isoformat(),
         "recording_end_utc":None if last is None else datetime.fromtimestamp(last,timezone.utc).isoformat(),
         "runway_data_error":runway_error,"approach_detections_by_runway_end":dict(Counter(runway for _aircraft,runway,_first in sample_counts)),
         "approach_counts_by_hour":{f"{hour} {end}":count for (hour,end),count in sorted(hourly.items())},
         "approach_track_count":len(sample_counts),"samples_per_track":{f"{a}:{r}:{first:.3f}":n for (a,r,first),n in sample_counts.items()},
         "track_summaries":{f"{a}:{r}:{first:.3f}":data for (a,r,first),data in track_summaries.items()},
         "approach_diagnostics":sorted(diagnostics,key=lambda item:(item["first_seen_ts"],item["runway_end"],item["icao24"])),
         "distinct_position_samples_distribution":distribution(sample_distribution),
         "position_time_span_seconds_distribution":distribution(span_distribution),
         "too_few_samples":len(too_few),"corridor_segments_fewer_than_two_samples":len(insufficient_corridor_segments),
         "inferred_activity_counts":{key:len(value) for key,value in inferred_activities.items()},
         "occupancy_status_observations":dict(occupancy),"low_altitude_received_reports":low_samples,
         "occupancy_assessable":occupancy_assessable,"occupancy_coverage_gate":gate,
         "occupancy_note":gate["reason"]}
    print(json.dumps({key:out[key] for key in ("cycles","recording_start_utc","recording_end_utc",
        "runway_data_error","approach_detections_by_runway_end","approach_counts_by_hour",
        "approach_track_count","too_few_samples","occupancy_status_observations",
        "inferred_activity_counts","occupancy_assessable","occupancy_coverage_gate","occupancy_note")},indent=2))
    output=Path(args.output_dir); output=output if output.is_absolute() else ROOT/output
    output.mkdir(parents=True,exist_ok=True)
    (output/"runway_stats.json").write_text(json.dumps(out,indent=2),encoding="utf-8")
    with (output/"approaches_per_hour.csv").open("w",encoding="utf-8") as stream:
        stream.write("utc_hour,runway_end,observations\n")
        for (hour,end_key),count in sorted(hourly.items()): stream.write(f'"{hour}",{end_key},{count}\n')
    with (output/"approach_track_diagnostics.csv").open("w",encoding="utf-8") as stream:
        stream.write("icao24,callsign,runway_end,first_seen_ts,last_seen_ts,distinct_position_samples,position_time_span_s,first_distance_nm,last_distance_nm,last_altitude_m,last_analysis_x_m,last_analysis_y_m,last_along_track_m,last_cross_track_m,last_glide_angle_deg,last_heading_difference_deg,last_vertical_rate_fpm,last_latitude,last_longitude,confidence,outcome\n")
        for item in out["approach_diagnostics"]:
            stream.write(",".join("" if item.get(key) is None else str(item[key]) for key in
                ("icao24","callsign","runway_end","first_seen_ts","last_seen_ts","distinct_position_samples","position_time_span_s",
                 "first_distance_to_threshold_nm","last_distance_to_threshold_nm","last_altitude_m","last_analysis_x_m","last_analysis_y_m",
                 "last_along_track_distance_m","last_cross_track_error_m","last_implied_glide_angle_deg","last_heading_difference_deg",
                 "last_vertical_rate_fpm","last_latitude","last_longitude","confidence","outcome"))+"\n")
    with (output/"runway_stats.md").open("w",encoding="utf-8") as stream:
        stream.write("# Recorded runway approach statistics\n\n")
        stream.write(f"- Replayed poll cycles: {source.total_cycles}\n- Approach track count: {len(sample_counts)}\n- Approach tracks with fewer than two distinct position timestamps: {len(too_few)}\n")
        stream.write(f"- Corridor participation segments with fewer than two distinct positions (separate metric): {len(insufficient_corridor_segments)}\n")
        stream.write(f"- Historical inference candidates: {out['inferred_activity_counts']} (not ground truth)\n")
        stream.write(f"- Distinct approach position-sample distribution: {distribution(sample_distribution)}\n")
        stream.write("\n## Per-track diagnostics\n\n| ICAO24 | Callsign | Runway end | Distinct timestamps | Span (s) | First NM | Last NM | Altitude m | Along m | Cross m | Glide ° | Confidence | Outcome |\n|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|\n")
        for item in out["approach_diagnostics"]:
            stream.write("| " + " | ".join(str(item.get(key) if item.get(key) is not None else "Unknown") for key in
                ("icao24","callsign","runway_end","distinct_position_samples","position_time_span_s",
                 "first_distance_to_threshold_nm","last_distance_to_threshold_nm","last_altitude_m",
                 "last_along_track_distance_m","last_cross_track_error_m","last_implied_glide_angle_deg","confidence","outcome"))+" |\n")
        stream.write(f"- Experimental occupancy assessable: {'Yes' if occupancy_assessable else 'No'}\n- Coverage gate: {gate['reason']}\n")
        if runway_error: stream.write(f"- Runway geometry unavailable: {runway_error}\n")
        stream.write("\nReceived observations do not establish aircraft absence.\n")
    print(f"Wrote replay statistics to {output}")
    if not runways: print(f"Runway-dependent statistics unavailable: {runway_error}")
    print("Counts reflect received reports only; missing ADS-B reports do not establish absence.")

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start",help="UTC ISO time (e.g. 2026-10-03T10:00:00Z)")
    parser.add_argument("--end",help="UTC ISO time")
    parser.add_argument("--db",default="data/airwatch.db")
    parser.add_argument("--output-dir",default="reports/runway_stats")
    asyncio.run(run(parser.parse_args()))

if __name__=="__main__": main()
