#!/usr/bin/env python3
"""Print local runway metadata and validation warnings for chart review."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml
from shapely.geometry import LineString
from backend.airwatch.runways import RunwayDataError, load_runways, runway_report_rows
from backend.airwatch.geometry import heading_difference


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    with (ROOT / "config.yaml").open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    settings = dict(config["runways"])
    settings["data_file"] = ROOT / settings["data_file"]
    settings["override_file"] = ROOT / settings["override_file"]
    try:
        runways = load_runways(airport_ident=config["airport"]["icao"],
                               latitude=float(config["airport"]["latitude"]),
                               longitude=float(config["airport"]["longitude"]), settings=settings)
    except RunwayDataError as error:
        print(f"Runway verification unavailable: {error}", file=sys.stderr)
        return 2
    by_pair = {runway.pair_identifier: runway for runway in runways}
    for item in runway_report_rows(runways):
        magnetic = item["magnetic_heading_deg"]
        designator = item["magnetic_designator_heading_deg"]
        runway = by_pair[item["runway"]]
        source_heading = None
        with (ROOT / config["runways"]["data_file"]).open(newline="", encoding="utf-8-sig") as stream:
            import csv
            source_heading_field = "le_heading_degT" if item["identifier"] == runway.end_a.identifier else "he_heading_degT"
            source_heading = next((row.get(source_heading_field) for row in csv.DictReader(stream)
                                   if row.get("airport_ident") == config["airport"]["icao"]
                                   and row.get("le_ident") == runway.end_a.identifier
                                   and row.get("he_ident") == runway.end_b.identifier), None)
        physical = f"{item['latitude']:.7f}, {item['longitude']:.7f}"
        landing = f"{item['landing_threshold_latitude']:.7f}, {item['landing_threshold_longitude']:.7f}"
        print(f"{item['runway']} end {item['identifier']} | physical end={physical} | landing threshold={landing} | "
              f"displaced={item['displaced_threshold_m'] / 0.3048:.0f} ft | "
              f"length={item['length_m'] / 0.3048:.0f} ft ({item['length_m']:.1f} m) | "
              f"width={item['width_m'] / 0.3048:.0f} ft ({item['width_m']:.1f} m) | surface={runway.surface} | "
              f"true={item['true_heading_deg']:.1f}° | magnetic={'UNCONFIRMED' if magnetic is None else f'{magnetic:.1f}°'} | magnetic designator="
              f"{'n/a' if designator is None else f'{designator:.0f}°'} | OurAirports heading_degT={source_heading or 'n/a'}")
        for warning in item["warnings"]:
            print(f"  WARNING: {warning}")
    print("\nRunway geometry overlap checks:")
    for pair_a, end_a_name, pair_b, end_b_name in (("07/25", "25", "12/30", "30"), ("07/25", "07", "12/30", "12")):
        ra, rb = by_pair[pair_a], by_pair[pair_b]
        ea = next(end for end in (ra.end_a, ra.end_b) if end.identifier == end_a_name)
        eb = next(end for end in (rb.end_a, rb.end_b) if end.identifier == end_b_name)
        center_a = LineString([(ra.end_a.x_m, ra.end_a.y_m), (ra.end_b.x_m, ra.end_b.y_m)])
        center_b = LineString([(rb.end_a.x_m, rb.end_a.y_m), (rb.end_b.x_m, rb.end_b.y_m)])
        crossing = center_a.intersection(center_b)
        corridor_overlap = ea.corridor_polygon.intersection(eb.corridor_polygon)
        runway_overlap = ra.buffer_polygon.intersection(rb.buffer_polygon)
        if crossing.geom_type == "Point":
            crossing_text = f"({crossing.x:.1f} m E, {crossing.y:.1f} m N ENU)"
            da = ((crossing.x - (ea.landing_threshold_x_m or ea.x_m)) ** 2 + (crossing.y - (ea.landing_threshold_y_m or ea.y_m)) ** 2) ** 0.5
            db = ((crossing.x - (eb.landing_threshold_x_m or eb.x_m)) ** 2 + (crossing.y - (eb.landing_threshold_y_m or eb.y_m)) ** 2) ** 0.5
            crossing_text += f"; {da:.1f} m from {end_a_name} landing threshold, {db:.1f} m from {end_b_name} landing threshold"
        else:
            crossing_text = f"centerline intersection geometry: {crossing.geom_type}"
        diff = heading_difference(ea.true_heading_deg, eb.true_heading_deg)
        print(f"  {end_a_name}/{end_b_name}: centerline {crossing_text}; buffer intersection={runway_overlap.area:.1f} m²; "
              f"approach-corridor intersection={corridor_overlap.area:.1f} m²; true-heading difference={diff:.1f}°; "
              f"max_heading_diff_deg={config['approach']['max_heading_diff_deg']}° => heading windows do not overlap")
    print("Compare these values with the official AIP / aerodrome chart before use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
