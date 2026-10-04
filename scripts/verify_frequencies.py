#!/usr/bin/env python3
"""Print selected local VOMM OurAirports frequency rows for official AIP review."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml
from backend.airwatch.frequencies import load_frequencies, estimate_facility_roles


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    settings = config["frequencies"]
    records = load_frequencies(airport_ident=config["airport"]["icao"],
        data_path=ROOT / settings["data_file"], override_path=ROOT / settings["override_file"])
    for row in records:
        print(f"{row['type']} | {row.get('description') or 'n/a'} | {row.get('frequency_mhz')} MHz | verify against the AIP")
    estimate = estimate_facility_roles(records, settings.get("departure_fallback_type", "APP"))
    departure = estimate["departure_estimate"]
    print(f"Departure facility estimate: source={departure['source_type'] or 'unavailable'}; "
          f"fallback={departure['is_fallback']}; {departure['note']}")
    clearance = estimate["clearance_estimate"]
    print(f"Clearance facility estimate: source={clearance['source_type'] or 'unavailable'}; "
          f"fallback={clearance['is_fallback']}; {clearance['note']}")
    print(estimate["verification"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
