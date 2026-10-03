"""Headless deterministic evaluation over application-recorded poll cycles."""
from __future__ import annotations

import asyncio
import csv
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Mapping

from pyproj import Transformer

from .alerts import AlertManager
from .clock import ReplayClock
from .models import AirportCenter
from .pairs import compute_pair_cpas
from .replay import ReplaySource
from .state_manager import AircraftStateManager


async def evaluate_recorded_range(*, db_path: str | Path, start: float, end: float,
                                  config: Mapping, profile: str) -> dict:
    if profile not in config["risk"]["profiles"]:
        raise ValueError(f"Unknown risk profile: {profile}")
    source = ReplaySource(db_path, start_time=start, end_time=end)
    settings = json.loads(json.dumps(config))
    settings["risk"]["active_profile"] = profile
    airport = settings["airport"]
    center = AirportCenter(airport["icao"], float(airport["latitude"]), float(airport["longitude"]))
    projection = f"+proj=aeqd +lat_0={center.latitude} +lon_0={center.longitude} +datum=WGS84 +units=m +no_defs"
    transformer = Transformer.from_crs("EPSG:4326", projection, always_xy=True)
    inverse = Transformer.from_crs(projection, "EPSG:4326", always_xy=True)
    state_settings = settings["state_manager"]
    manager = AircraftStateManager(
        latitude=center.latitude, longitude=center.longitude,
        history_length=int(state_settings["history_length"]),
        low_quality_after_s=float(state_settings["low_quality_after_s"]),
        drop_after_s=float(state_settings["drop_after_s"]), tombstone_s=float(state_settings["tombstone_s"]),
        low_altitude_ft=float(settings["collector"]["low_altitude_ft"]),
        smoothing_enabled=bool(state_settings["smoothing_enabled"]), alpha=float(state_settings["alpha"]),
        beta=float(state_settings["beta"]), transform=transformer)
    alerts = AlertManager(settings=settings["risk"], airport=center.icao, mode="REPLAY", risk_profile=profile)
    clock = ReplayClock()
    cycles, pairs_evaluated, aircraft_hours = 0, 0, 0.0
    previous_time: float | None = None
    previous_aircraft_count = 0
    first_cycle_time: float | None = None
    last_cycle_time: float | None = None
    closest: dict[str, dict] = {}
    event_records: dict[str, dict] = {}
    while not source.finished:
        timestamp = source.next_fetch_time
        clock.set(timestamp)
        first_cycle_time = timestamp if first_cycle_time is None else first_cycle_time
        last_cycle_time = timestamp
        observations = await source.fetch_states(center, float(airport["radius_nm"]))
        manager.update(observations, clock.now())
        states = manager.snapshot(clock.now(), include_unpositioned=True)
        states_by_id = {state["icao24"].lower(): state for state in states}
        pair_result = compute_pair_cpas(
            states, airport_radius_nm=float(airport["radius_nm"]), cpa_settings=settings["cpa"],
            prediction_settings=settings["prediction"], inverse_transformer=inverse)
        pairs_evaluated += pair_result["pairs_returned"]
        for pair in pair_result["pairs"]:
            old = closest.get(pair["pair_key"])
            if old is None or pair["h_sep_cpa_m"] < old["h_sep_cpa_m"]:
                closest[pair["pair_key"]] = pair
        changes = alerts.process_cycle(pair_result["pairs"], states, clock.now())
        for change in changes:
            event = change.get("event", change)
            event_records[event["event_id"]] = event
        if previous_time is not None:
            aircraft_hours += previous_aircraft_count * max(0.0, timestamp - previous_time) / 3600.0
        previous_time = timestamp
        previous_aircraft_count = sum(state["latitude"] is not None and state["longitude"] is not None for state in states)
        cycles += 1

    for event in (*alerts.open_events.values(), *alerts.last_events.values()):
        event_records[event["event_id"]] = event
    events = [event for event in event_records.values() if event.get("opened_ts") is not None]
    events.sort(key=lambda event: (event["first_seen_ts"], event["event_id"]))
    actual_start = start if first_cycle_time is None else first_cycle_time
    actual_end = end if last_cycle_time is None else last_cycle_time
    time_span_s = max(0.0, actual_end - actual_start)
    hours = time_span_s / 3600.0
    durations = [max(0.0, float(event.get("resolved_ts") or event["last_updated_ts"]) - float(event["first_seen_ts"]))
                 for event in events]
    peak_counts = Counter(event["peak_risk"] for event in events)
    per_hour = {level: (peak_counts[level] / hours if hours else 0.0)
                for level in ("LOW", "MEDIUM", "HIGH", "CRITICAL")}
    data_lost = sum(event.get("resolution_reason") == "data_lost" for event in events)
    low_confidence = sum(event.get("confidence") == "LOW" for event in events)
    return {
        "mode": "REPLAY", "profile": profile, "start": actual_start, "end": actual_end,
        "poll_cycles": cycles, "aircraft_hours": aircraft_hours,
        "pairs_evaluated": pairs_evaluated, "alerts_opened_per_hour_by_peak_risk": per_hour,
        "events_count": len(events), "median_alert_duration_s": statistics.median(durations) if durations else None,
        "max_alert_duration_s": max(durations) if durations else None,
        "data_lost_events": data_lost,
        "low_confidence_share": low_confidence / len(events) if events else 0.0,
        "events": events,
        "closest_pairs": sorted(closest.values(), key=lambda pair: (pair["h_sep_cpa_m"], pair["pair_key"]))[:10],
    }


def write_evaluation_report(report: Mapping, output_dir: str | Path) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    csv_path, md_path = output / "alert_events.csv", output / "alert_evaluation.md"
    columns = ("event_id", "event_type", "status", "mode", "risk_profile", "pair_key", "first_seen_ts",
               "last_updated_ts", "resolved_ts", "resolution_reason", "current_risk", "peak_risk",
               "min_h_sep_cpa_m", "min_v_sep_cpa_m", "min_time_to_cpa_s", "confidence", "cycles_observed")
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({column: event.get(column) for column in columns} for event in report["events"])
    lines = ["# Recorded ADS-B alert evaluation", "", "Research evaluation using app-recorded observations only.",
             "", f"- Mode: `{report['mode']}`", f"- Risk profile: `{report['profile']}`",
             f"- Range: `{report['start']:.3f}` – `{report['end']:.3f}` UTC epoch seconds",
             f"- Poll cycles: {report['poll_cycles']}", f"- Aircraft-hours: {report['aircraft_hours']:.3f}",
             f"- Pairs evaluated: {report['pairs_evaluated']}",
             f"- Median/max alert duration: {report['median_alert_duration_s']} / {report['max_alert_duration_s']} s",
             f"- Alerts resolved after data loss: {report['data_lost_events']}",
             f"- LOW-confidence event share: {report['low_confidence_share']:.1%}", "",
             "## Alerts per hour by peak risk", "", "| Level | Events per hour |", "|---|---:|"]
    lines.extend(f"| {level} | {rate:.3f} |" for level, rate in report["alerts_opened_per_hour_by_peak_risk"].items())
    lines.extend(["", "## Ten closest pair records", "", "| Pair | Horizontal CPA (m) | CPA time (s) |", "|---|---:|---:|"])
    lines.extend(f"| {pair['pair_key']} | {pair['h_sep_cpa_m']:.1f} | {pair['t_cpa_s']:.1f} |"
                 for pair in report["closest_pairs"])
    lines.extend(["", "## Tuning note", "",
                  "Use the `research_default` profile to inspect alert volume and review event snapshots against recorded observations. "
                  "Tune one threshold at a time, rerun the identical time range, and compare event counts, duration, confidence, and closest-pair rows. "
                  "For ordinary traffic, the goal is a low false-alert rate, not finding conflicts. The `sensitive_test` profile is deliberately loose and its alerts are not research results.", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, md_path
