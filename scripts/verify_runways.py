#!/usr/bin/env python3
"""Print local runway metadata and validation warnings for chart review."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml
from backend.airwatch.runways import RunwayDataError, load_runways, runway_report_rows


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
    for item in runway_report_rows(runways):
        magnetic = item["magnetic_heading_deg"]
        designator = item["magnetic_designator_heading_deg"]
        print(f"{item['runway']} end {item['identifier']} | threshold={item['latitude']:.7f}, {item['longitude']:.7f} | "
              f"length={item['length_m']:.1f} m | width={item['width_m']:.1f} m | "
              f"true={item['true_heading_deg']:.1f}° | magnetic={magnetic:.1f}° | magnetic designator="
              f"{'n/a' if designator is None else f'{designator:.0f}°'}")
        for warning in item["warnings"]:
            print(f"  WARNING: {warning}")
    print("Compare these values with the official AIP / aerodrome chart before use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
