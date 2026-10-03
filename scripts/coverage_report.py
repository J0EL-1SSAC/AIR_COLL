#!/usr/bin/env python3
"""Build CSV and Markdown surveillance-coverage reports from recorded live data."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml
from backend.airwatch.coverage_analysis import analyze_coverage, render_markdown, write_csv
from backend.airwatch.runways import RunwayDataError, load_runways


def parse_utc(value: str | None) -> float | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamps must include Z or a UTC offset")
    return parsed.astimezone(timezone.utc).timestamp()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="optional UTC ISO timestamp, e.g. 2026-10-03T10:00:00Z")
    parser.add_argument("--end", help="optional UTC ISO timestamp")
    parser.add_argument("--db", help="SQLite database (defaults to storage.database_path in config.yaml)")
    parser.add_argument("--output-dir", help="output directory (defaults to coverage_report.output_dir in config.yaml)")
    args = parser.parse_args()
    try:
        start, end = parse_utc(args.start), parse_utc(args.end)
    except (ValueError, argparse.ArgumentTypeError) as error:
        parser.error(str(error))
    if start is not None and end is not None and start > end:
        parser.error("--start must be earlier than --end")
    with (ROOT / "config.yaml").open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    runway_settings = dict(config["runways"])
    runway_settings["data_file"] = ROOT / runway_settings["data_file"]
    runway_settings["override_file"] = ROOT / runway_settings["override_file"]
    try:
        runways = load_runways(airport_ident=config["airport"]["icao"],
                               latitude=float(config["airport"]["latitude"]),
                               longitude=float(config["airport"]["longitude"]), settings=runway_settings)
        runway_issue = None
    except RunwayDataError as error:
        runways = None
        runway_issue = str(error)
    db_path = Path(args.db or config["storage"]["database_path"])
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    output_dir = Path(args.output_dir or config["coverage_report"]["output_dir"])
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    report = analyze_coverage(db_path=db_path, airport_latitude=float(config["airport"]["latitude"]),
                              airport_longitude=float(config["airport"]["longitude"]),
                              airport_elevation_m=float(config["coverage_report"]["ground_elevation_m"]),
                              radius_nm=float(config["airport"]["radius_nm"]),
                              settings=config["coverage_report"], runways=runways,
                              start=start, end=end)
    report["runway_data_issue"] = runway_issue
    report["max_poll_gap_s"] = float(config["coverage_report"]["max_poll_gap_s"])
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "altitude_bands.csv", report["altitude_bands"])
    write_csv(output_dir / "distance_rings.csv", report["distance_rings"])
    write_csv(output_dir / "hourly_coverage.csv", report["hourly"])
    write_csv(output_dir / "fadeout_tracks.csv", report["fadeout"]["tracks_data"])
    write_csv(output_dir / "update_intervals.csv", [{"interval_s": value} for value in report["update_intervals_s"]["intervals"]])
    histogram_rows = []
    for track_type, histograms in report["fadeout"]["histograms"].items():
        histogram_rows.extend({"track_type": track_type, "dimension": "altitude", **item}
                              for item in histograms["altitude_histogram"])
        if histograms["threshold_distance_histogram"] is not None:
            histogram_rows.extend({"track_type": track_type, "dimension": "threshold_distance", **item}
                                  for item in histograms["threshold_distance_histogram"])
    write_csv(output_dir / "fadeout_histograms.csv", histogram_rows)
    write_csv(output_dir / "approach_corridors.csv", report["approach_corridors"])
    write_csv(output_dir / "runway_buffer_reports.csv", [
        {"runway": key, "reports": value} for key, value in report["runway_buffer_reports"].items()])
    report["update_intervals_s"].pop("intervals", None)
    (output_dir / "coverage_report.md").write_text(render_markdown(report), encoding="utf-8")
    (output_dir / "coverage_summary.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Wrote coverage report and CSV files to {output_dir}")
    if runway_issue:
        print(f"Runway geometry unavailable: {runway_issue}")
    if report["duration_hours"] < float(config["coverage_report"]["feasibility"]["minimum_recording_hours"]):
        print("INSUFFICIENT DATA: recording is shorter than the configured minimum duration.")
    else:
        print(f"Analyzed {report['recording']['poll_cycles']} poll cycles and {report['recording']['reports']} recorded state reports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
