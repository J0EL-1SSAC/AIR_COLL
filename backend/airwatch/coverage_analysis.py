"""Read-only coverage analysis for app-recorded source observations."""
from __future__ import annotations

import csv
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from shapely.geometry import Point

from .geometry import NM_TO_M, nearest_runway_end
from .runways import Runway, RunwayEndGeometry

ALTITUDE_M_PER_FT = 0.3048


def _ro_connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)


def _band(value: float, bands) -> str | None:
    for low, high, name in bands:
        if low <= value < high:
            return name
    return None


def _rows_as_dicts(cursor, rows):
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in rows]


def analyze_coverage(*, db_path: Path, airport_latitude: float, airport_longitude: float,
                     airport_elevation_m: float, radius_nm: float, settings: dict,
                     runways: list[Runway] | None, start: float | None = None,
                     end: float | None = None) -> dict:
    """Analyze only rows already stored in raw_states; database is opened read-only."""
    params = []
    clauses = []
    if start is not None:
        clauses.append("fetch_time >= ?")
        params.append(start)
    if end is not None:
        clauses.append("fetch_time <= ?")
        params.append(end)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _ro_connect(db_path) as connection:
        connection.execute("BEGIN")
        cursor = connection.execute("SELECT fetch_time, icao24, callsign, latitude, longitude, baro_altitude_m, geo_altitude_m, velocity_mps, track_deg, vertical_rate_mps, on_ground, position_timestamp, last_contact FROM raw_states" + where + " ORDER BY fetch_time, icao24", params)
        rows = _rows_as_dicts(cursor, cursor.fetchall())
        cursor = connection.execute("SELECT sample_time, aircraft_count, below_threshold_count, on_ground_count FROM coverage_samples" +
                                    (" WHERE sample_time >= ?" if start is not None else "") +
                                    (" AND sample_time <= ?" if start is not None and end is not None else " WHERE sample_time <= ?" if start is None and end is not None else "") +
                                    " ORDER BY sample_time", ([v for v in (start, end) if v is not None]))
        polls = _rows_as_dicts(cursor, cursor.fetchall())
    latitude, longitude = airport_latitude, airport_longitude
    from .geometry import local_transformers
    forward, _ = local_transformers(latitude, longitude)
    radius_m = radius_nm * NM_TO_M
    runway_list = runways or []
    altitude_bands = [(float(low), math.inf if high is None else float(high), name)
                      for low, high, name in settings["altitude_bands_ft"]]
    distance_bands = [(float(low), float(high), name) for low, high, name in settings["distance_rings_nm"]]
    runway_ends: list[RunwayEndGeometry] = []
    for runway in runway_list:
        runway_ends.extend([RunwayEndGeometry(end.identifier,
                            end.landing_threshold_x_m if end.landing_threshold_x_m is not None else end.x_m,
                            end.landing_threshold_y_m if end.landing_threshold_y_m is not None else end.y_m,
                            end.true_heading_deg) for end in (runway.end_a, runway.end_b)])
    altitude_counts = Counter()
    altitude_aircraft = defaultdict(set)
    distance_counts = Counter()
    distance_aircraft = defaultdict(set)
    missing = Counter()
    position_count = 0
    ages, intervals = [], []
    pos_by_aircraft = defaultdict(list)
    hourly = defaultdict(lambda: {"aircraft": set(), "low": 0, "ground": 0, "reports": 0})
    runway_buffer_reports = Counter()
    runway_buffer_low_reports = 0
    runway_buffer_ground_reports = 0
    corridor_reports = Counter()
    corridor_aircraft = defaultdict(set)
    for row in rows:
        fetch = float(row["fetch_time"])
        icao = row["icao24"]
        hour = int(fetch // 3600) * 3600
        h = hourly[hour]
        h["reports"] += 1
        h["aircraft"].add(icao)
        h["ground"] += int(row["on_ground"] == 1)
        pos_ok = row["latitude"] is not None and row["longitude"] is not None
        alt_m = row["geo_altitude_m"] if row["geo_altitude_m"] is not None else row["baro_altitude_m"]
        missing["position"] += int(not pos_ok)
        missing["altitude"] += int(alt_m is None)
        missing["velocity"] += int(row["velocity_mps"] is None)
        missing["on_ground"] += int(row["on_ground"] is None)
        if row["position_timestamp"] is not None:
            ages.append(max(0.0, fetch - float(row["position_timestamp"])))
        x = y = distance_m = None
        inside = False
        if pos_ok:
            position_count += 1
            x, y = forward.transform(float(row["longitude"]), float(row["latitude"]))
            distance_m = math.hypot(x, y)
            inside = distance_m <= radius_m
            if row["position_timestamp"] is not None:
                pos_by_aircraft[icao].append((float(row["position_timestamp"]), fetch, x, y, alt_m,
                                             row["on_ground"], row["callsign"], inside, distance_m))
        alt_agl_m = None if alt_m is None else float(alt_m) - float(airport_elevation_m)
        if row["on_ground"] == 1:
            altitude_counts["on ground"] += 1
            altitude_aircraft["on ground"].add(icao)
        elif alt_agl_m is not None:
            feet = alt_agl_m / ALTITUDE_M_PER_FT
            band = _band(feet, altitude_bands)
            if band:
                altitude_counts[band] += 1
                altitude_aircraft[band].add(icao)
                if feet < 1000:
                    h["low"] += 1
        if inside and distance_m is not None:
            band = _band(distance_m / NM_TO_M, distance_bands)
            if band:
                distance_counts[band] += 1
                distance_aircraft[band].add(icao)
        if inside and x is not None:
            for runway in runway_list:
                if runway.buffer_polygon.covers(Point(x, y)):
                    runway_buffer_reports[runway.pair_identifier] += 1
                    runway_buffer_ground_reports += int(row["on_ground"] == 1)
                    runway_buffer_low_reports += int(row["on_ground"] != 1 and alt_agl_m is not None and
                        alt_agl_m <= float(settings.get("runway_buffer_low_altitude_ft", 1000)) * ALTITUDE_M_PER_FT)
                for end in (runway.end_a, runway.end_b):
                    if (end.corridor_polygon.covers(Point(x, y)) and
                            alt_agl_m is not None and alt_agl_m <= float(settings["approach_altitude_ceiling_m"])):
                        key = f"{runway.pair_identifier}:{end.identifier}"
                        corridor_reports[key] += 1
                        corridor_aircraft[key].add(icao)

    tracks = []
    max_gap = float(settings["max_track_gap_s"])
    for icao, observations in pos_by_aircraft.items():
        observations.sort(key=lambda item: item[0])
        distinct = []
        seen_timestamps = set()
        for observation in observations:
            if observation[0] not in seen_timestamps:
                distinct.append(observation)
                seen_timestamps.add(observation[0])
        intervals.extend(b[0] - a[0] for a, b in zip(distinct, distinct[1:]) if b[0] > a[0])
        chunk = []
        for observation in distinct:
            if not observation[7]:
                if len(chunk) >= int(settings["minimum_track_reports"]):
                    tracks.append((icao, chunk))
                chunk = []
                continue
            if chunk and observation[0] - chunk[-1][0] > max_gap:
                if len(chunk) >= int(settings["minimum_track_reports"]):
                    tracks.append((icao, chunk))
                chunk = []
            chunk.append(observation)
        if len(chunk) >= int(settings["minimum_track_reports"]):
            tracks.append((icao, chunk))

    fades = []
    ambiguous = 0
    arrivals = departures = 0
    for icao, track in tracks:
        first, last = track[0], track[-1]
        altitudes = [item[4] for item in track if item[4] is not None]
        if len(altitudes) < 2:
            classification = "ambiguous"
        else:
            first_alt, last_alt = altitudes[0], altitudes[-1]
            first_distance, last_distance = first[8], last[8]
            if last_alt < first_alt and last_distance < first_distance:
                classification = "arrival"
                arrivals += 1
            elif last_alt > first_alt and last_distance > first_distance:
                classification = "departure"
                departures += 1
            else:
                classification = "ambiguous"
        if classification == "ambiguous":
            ambiguous += 1
        terminal = last if classification == "arrival" else first
        end_distance = None
        end_identifier = None
        if runway_ends:
            nearest, end_distance = nearest_runway_end(terminal[2], terminal[3], runway_ends)
            end_identifier = nearest.identifier
        altitude_ft = None if terminal[4] is None else max(0.0, (terminal[4] - float(airport_elevation_m)) / ALTITUDE_M_PER_FT)
        runway_buffer_hit = False
        if runway_list:
            point = Point(terminal[2], terminal[3])
            runway_buffer_hit = any(runway.buffer_polygon.covers(point) for runway in runway_list)
        fades.append({"icao24": icao, "classification": classification,
                      "report_count": len(track), "altitude_ft": altitude_ft,
                      "distance_from_airport_nm": terminal[8] / NM_TO_M,
                      "nearest_threshold_identifier": end_identifier,
                      "distance_from_nearest_threshold_nm": None if end_distance is None else end_distance / NM_TO_M,
                      "runway_buffer": runway_buffer_hit})
    total = len(rows)
    span_s = 0 if not polls else max(0.0, polls[-1]["sample_time"] - polls[0]["sample_time"])
    poll_gaps = [polls[i]["sample_time"] - polls[i - 1]["sample_time"] for i in range(1, len(polls))]
    gaps = [{"start": polls[i - 1]["sample_time"], "end": polls[i]["sample_time"], "duration_s": gap}
            for i, gap in enumerate(poll_gaps, 1) if gap > float(settings["max_poll_gap_s"])]
    hour_rows = [{"hour_utc_epoch": hour, "distinct_aircraft": len(value["aircraft"]),
                  "low_altitude_reports": value["low"], "on_ground_reports": value["ground"],
                  "mean_low_altitude_reports_per_poll": value["low"] / max(1, sum(1 for p in polls if hour <= p["sample_time"] < hour + 3600)),
                  "mean_on_ground_reports_per_poll": value["ground"] / max(1, sum(1 for p in polls if hour <= p["sample_time"] < hour + 3600)),
                  "reports": value["reports"]} for hour, value in sorted(hourly.items())]
    max_poll_gap_s = float(settings.get("max_poll_gap_s", 90))
    observed_s = sum(max(0.0, float(right["sample_time"])-float(left["sample_time"]))
                     for left,right in zip(polls,polls[1:])
                     if float(right["sample_time"])-float(left["sample_time"]) <= max_poll_gap_s)
    duration_hours = observed_s / 3600.0
    low_reports = sum(value for key, value in altitude_counts.items() if key != "on ground" and key in {"0-500 ft", "500-1,000 ft"})
    ground_reports = altitude_counts["on ground"]
    runway_reports = sum(runway_buffer_reports.values())
    fadeouts_by_type = {}
    for classification in ("arrival", "departure"):
        selected = [item for item in fades if item["classification"] == classification]
        fadeouts_by_type[classification] = {
            "altitude_histogram": [{"band": name,
                                    "tracks": sum(item["altitude_ft"] is not None and
                                                  name == _band(item["altitude_ft"], altitude_bands) for item in selected)}
                                   for name in [item[2] for item in altitude_bands]],
            "threshold_distance_histogram": ([{"band": name,
                                               "tracks": sum(name == _band(item["distance_from_nearest_threshold_nm"] if item["distance_from_nearest_threshold_nm"] is not None else -1, distance_bands) for item in selected)}
                                              for _lo, _hi, name in distance_bands] if runway_ends else None),
        }
    report = {"source_db": str(db_path), "recording": {"first_fetch_ts": None if not polls else polls[0]["sample_time"],
              "last_fetch_ts": None if not polls else polls[-1]["sample_time"], "span_s": span_s,
              "observed_s": observed_s, "observed_hours": duration_hours,
              "poll_cycles": len(polls), "poll_gap_count": len(gaps), "poll_gaps": gaps,
              "distinct_aircraft": len({row["icao24"] for row in rows}), "reports": total},
              "altitude_basis": "geometric altitude when available per report, otherwise barometric altitude; airport elevation subtracted",
              "altitude_bands": [{"band": name, "reports": altitude_counts[name],
                                  "distinct_aircraft": len(altitude_aircraft[name])}
                                 for name in ["on ground"] + [item[2] for item in altitude_bands]],
              "distance_rings": [{"ring": name, "reports": distance_counts[name],
                                  "distinct_aircraft": len(distance_aircraft[name])}
                                 for _lo, _hi, name in distance_bands],
              "missing_shares": {name: {"count": missing[name], "share": missing[name] / total if total else None}
                                 for name in ("position", "altitude", "velocity", "on_ground")},
              "position_reports": position_count,
              "update_intervals_s": {"count": len(intervals), "median": median(intervals) if intervals else None,
                                     "min": min(intervals) if intervals else None,
                                     "max": max(intervals) if intervals else None,
                                     "intervals": intervals,
                                     "p90": sorted(intervals)[min(len(intervals)-1, math.floor((len(intervals)-1)*0.9))] if intervals else None},
              "median_data_age_s": median(ages) if ages else None,
              "fadeout": {"tracks": len(tracks), "arrivals": arrivals, "departures": departures,
                          "ambiguous": ambiguous, "tracks_data": fades,
                          "histograms": fadeouts_by_type,
                          "arrival_median_altitude_ft": median([f["altitude_ft"] for f in fades if f["classification"] == "arrival" and f["altitude_ft"] is not None]) if any(f["classification"] == "arrival" and f["altitude_ft"] is not None for f in fades) else None,
                          "arrival_median_threshold_nm": median([f["distance_from_nearest_threshold_nm"] for f in fades if f["classification"] == "arrival" and f["distance_from_nearest_threshold_nm"] is not None]) if any(f["classification"] == "arrival" and f["distance_from_nearest_threshold_nm"] is not None for f in fades) else None},
              "runways_available": bool(runway_list), "on_ground_aircraft_count": len({row["icao24"] for row in rows if row["on_ground"] == 1}),
              "runway_buffer_reports": dict(runway_buffer_reports),
              "runway_buffer_report_total": runway_reports if runway_list else None,
              "runway_buffer_low_altitude_reports": runway_buffer_low_reports if runway_list else None,
              "runway_buffer_on_ground_reports": runway_buffer_ground_reports if runway_list else None,
              "approach_corridors": [{"corridor": key, "reports_below_ceiling": corridor_reports[key],
                                      "distinct_aircraft": len(corridor_aircraft[key])} for key in sorted(corridor_reports)],
              "tracks_near_runway_buffer": {"arrivals": sum(f["classification"] == "arrival" and f["runway_buffer"] for f in fades),
                                            "departures": sum(f["classification"] == "departure" and f["runway_buffer"] for f in fades)},
              "hourly": hour_rows, "duration_hours": duration_hours,
              "low_altitude_reports_per_hour": low_reports / max(duration_hours, 1e-9),
              "on_ground_reports_per_hour": ground_reports / max(duration_hours, 1e-9),
              "runway_buffer_reports_per_hour": (runway_reports / max(duration_hours, 1e-9)
                                                  if runway_list else None),
              "runway_buffer_low_altitude_ft": float(settings.get("runway_buffer_low_altitude_ft", 1000)),
              "runway_buffer_low_altitude_reports_per_hour": (runway_buffer_low_reports / max(duration_hours, 1e-9) if runway_list else None),
              "runway_buffer_on_ground_reports_per_hour": (runway_buffer_ground_reports / max(duration_hours, 1e-9) if runway_list else None)}
    report["recommendations"] = _recommendations(report, settings)
    return report


def _recommendations(report: dict, settings: dict) -> dict:
    minimum_hours = float(settings["feasibility"]["minimum_recording_hours"])
    if report["duration_hours"] < minimum_hours:
        return {name: "insufficient data" for name in ("approach_monitoring", "runway_occupancy", "runway_crossing")}
    if not report["runways_available"]:
        return {name: "insufficient data: runway geometry unavailable"
                for name in ("approach_monitoring", "runway_occupancy", "runway_crossing")}
    feasible_approach = report["low_altitude_reports_per_hour"] >= float(settings["feasibility"]["minimum_low_altitude_reports_per_hour"])
    feasible_occupancy = (report.get("runway_buffer_on_ground_reports_per_hour") is not None and
                          report["runway_buffer_on_ground_reports_per_hour"] >= float(settings["feasibility"]["minimum_ground_reports_per_hour"])
                          and report["runway_buffer_reports_per_hour"] >= float(settings["feasibility"]["minimum_runway_buffer_reports_per_hour"]))
    return {"approach_monitoring": "potentially supportable; verify low-altitude continuity and runway-specific coverage" if feasible_approach else "unreliable: low-altitude reports below configured minimum",
            "runway_occupancy": "potentially supportable only for research; ground/threshold coverage meets configured count criteria" if feasible_occupancy else "unreliable: ground or runway-buffer report rate below configured minimum",
            "runway_crossing": "unreliable for confirmation from public ADS-B alone; low-altitude/ground gaps and position error can hide crossings"}


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = sorted({key for row in rows for key in row}) if rows else []
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        if columns:
            writer.writeheader()
            writer.writerows(rows)


def render_markdown(report: dict) -> str:
    rec = report["recording"]
    lines = ["# ADS-B surveillance coverage report", "", "**Research coverage analysis. This measures what the recorded feed received; it cannot reveal aircraft the receivers did not see. Missing low-altitude or ground reports are not evidence that no aircraft were present.**", "",
             "## Recording summary", "", f"- Window: {rec['first_fetch_ts']} to {rec['last_fetch_ts']} UTC epoch seconds" if rec["first_fetch_ts"] is not None else "- Window: no recorded polls",
             f"- Duration: {report['duration_hours']:.2f} hours; {rec['poll_cycles']} poll cycles; {rec['reports']} state reports; {rec['distinct_aircraft']} distinct aircraft",
             f"- Poll gaps longer than configured {report.get('max_poll_gap_s', 'limit')} s: {rec['poll_gap_count']}",
             f"- Altitude basis: {report['altitude_basis']}", "", "## Altitude bands", "", "| Band above airport level | Reports | Distinct aircraft |", "|---|---:|---:|"]
    lines.extend(f"| {item['band']} | {item['reports']} | {item['distinct_aircraft']} |" for item in report["altitude_bands"])
    lines += ["", "## Distance from airport reference point", "", "| Ring | Reports | Distinct aircraft |", "|---|---:|---:|"]
    lines.extend(f"| {item['ring']} | {item['reports']} | {item['distinct_aircraft']} |" for item in report["distance_rings"])
    lines += ["", "## Missing fields and update intervals", "", "| Field | Missing reports | Share |", "|---|---:|---:|"]
    for key, value in report["missing_shares"].items():
        share = "n/a" if value["share"] is None else f"{100 * value['share']:.1f}%"
        lines.append(f"| {key} | {value['count']} | {share} |")
    interval = report["update_intervals_s"]
    lines += ["", f"- Distinct-position update interval median/max: {interval['median']} / {interval['max']} s (n={interval['count']})",
              f"- Median report age at fetch: {report['median_data_age_s']} s", "", "## Fade-out analysis", "",
              "Arrival/departure classification is heuristic: arrivals descend and get closer during the observed in-radius track; departures climb and get farther away. All other tracks are ambiguous. Values describe last/first received reports, not actual disappearance or runway events.", "",
              f"- Tracks analyzed: {report['fadeout']['tracks']}; arrivals: {report['fadeout']['arrivals']}; departures: {report['fadeout']['departures']}; ambiguous: {report['fadeout']['ambiguous']}",
              f"- Arrivals' median last-seen altitude: {report['fadeout']['arrival_median_altitude_ft']} ft above airport level",
              f"- Arrivals' median distance to nearest runway threshold: {report['fadeout']['arrival_median_threshold_nm'] if report['fadeout']['arrival_median_threshold_nm'] is not None else 'unavailable until runway geometry is loaded'} NM",
              "- Per-track values are in `fadeout_tracks.csv`; altitude and nearest-threshold distance histograms are below.", ""]
    for kind, histograms in report["fadeout"]["histograms"].items():
        lines.extend([f"### {kind.title()} fade-out histograms", "", "| Altitude band | Tracks |", "|---|---:|"])
        lines.extend(f"| {item['band']} | {item['tracks']} |" for item in histograms["altitude_histogram"])
        if histograms["threshold_distance_histogram"] is None:
            lines.extend(["", "Nearest-runway-threshold distance histogram unavailable until runway geometry is loaded."])
        else:
            lines.extend(["", "| Nearest-threshold distance | Tracks |", "|---|---:|"])
            lines.extend(f"| {item['band']} | {item['tracks']} |" for item in histograms["threshold_distance_histogram"])
        lines.append("")
    lines += ["## Ground and runway coverage", "",
              f"- Aircraft ever reported on ground: {report['on_ground_aircraft_count']}",
              f"- Reports in runway buffers: {report['runway_buffer_report_total'] if report['runways_available'] else 'unavailable: runway data file missing'}"]
    if report["approach_corridors"]:
        lines.extend(f"- {item['corridor']}: {item['reports_below_ceiling']} reports below ceiling; {item['distinct_aircraft']} distinct aircraft" for item in report["approach_corridors"])
    else:
        lines.append("- Approach-corridor metrics unavailable until runway geometry is loaded.")
    runway_rate = report["runway_buffer_reports_per_hour"]
    runway_rate_text = "unavailable until runway geometry is loaded" if runway_rate is None else f"{runway_rate:.2f} per recording hour"
    lines += ["", "## Per-hour variation", "", "Detailed UTC hour rows are in `hourly_coverage.csv`; counts are raw received reports and distinct aircraft in each hour.", "",
              "## Interpretation and feasibility", "", f"- Low-altitude reports (<1,000 ft above airport level): {report['low_altitude_reports_per_hour']:.2f} per recording hour.",
              f"- On-ground reports: {report['on_ground_reports_per_hour']:.2f} per recording hour.",
              f"- Runway-buffer reports: {runway_rate_text}.", ""]
    if report["runways_available"]:
        lines.extend([f"- Runway-buffer reports below configured {report.get('runway_buffer_low_altitude_ft', 1000)} ft: {report['runway_buffer_low_altitude_reports_per_hour']:.2f} per hour.",
                      f"- Runway-buffer on-ground reports: {report['runway_buffer_on_ground_reports_per_hour']:.2f} per hour."])
    else:
        lines.append("- Runway-buffer low-altitude and on-ground report rates unavailable until runway geometry is loaded.")
    lines.append("")
    lines.extend(f"- **{name.replace('_', ' ').title()}:** {value}." for name, value in report["recommendations"].items())
    if report.get("runway_data_issue"):
        lines += ["", f"- Runway source status: unavailable ({report['runway_data_issue']}).", ""]
    lines += ["", "## Chart verification", "", "Compare these runway values with the official AIP / aerodrome chart before use. Open-airport datasets can contain errors; discrepancies are warnings, not automatic corrections.", ""]
    return "\n".join(lines)
