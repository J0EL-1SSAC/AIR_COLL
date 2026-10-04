"""Offline OurAirports frequency reference and explicitly labeled role estimates."""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Mapping

import yaml

logger = logging.getLogger(__name__)
FREQUENCY_TYPES = ("ACC", "APP", "ATIS", "GCA", "GND", "TWR")


def estimate_controlling_facility(state: Mapping, approaches: list[Mapping], frequencies: Mapping,
                                  settings: Mapping) -> dict:
    """Rule-based facility estimate; this is never a radio observation."""
    requested = "ACC"
    reasons = ["default en-route facility estimate"]
    if state.get("on_ground") is True:
        requested, reasons = "GND", ["ADS-B on_ground flag is true"]
    else:
        own = [item for item in approaches if item.get("icao24") == state.get("icao24")]
        near_nm = min((float(item.get("distance_to_threshold_nm", 1e9)) for item in own), default=None)
        if own and near_nm is not None and near_nm <= float(settings["short_final_distance_nm"]):
            requested, reasons = "TWR", ["approach estimate is near the runway threshold"]
        elif own:
            requested, reasons = "APP", ["aircraft has a current approach estimate"]
        else:
            vertical_fpm = float(state.get("vertical_rate_mps") or 0) * 196.850394
            altitude_ft = state.get("altitude_ft")
            if (vertical_fpm >= float(settings["departure_climb_min_fpm"])
                    and state.get("distance_nm") is not None
                    and float(state["distance_nm"]) <= float(settings["departure_distance_nm"])
                    and altitude_ft is not None and float(altitude_ft) <= float(settings["departure_max_altitude_ft"])):
                requested, reasons = "DEP", ["climbing near the airport; DEP facility is estimated"]
    fallback_used = False
    actual = requested
    if requested not in frequencies:
        fallback = settings.get("departure_fallback_type", "APP") if requested == "DEP" else None
        if fallback and fallback in frequencies:
            actual, fallback_used = fallback, True
            reasons.append(f"No {requested} entry; falls back to {actual}")
        else:
            actual = "UNKNOWN"
            reasons.append(f"No {requested} frequency is listed")
    return {"requested_type": requested, "facility_type": actual,
            "frequencies": list(frequencies.get(actual, [])), "fallback": fallback_used,
            "reasons": reasons, "label": "Estimate. Not a radio observation."}


def estimate_aircraft_facilities(states: list[Mapping], approaches: list[Mapping],
                                 frequency_data: Mapping, settings: Mapping) -> dict:
    frequencies = frequency_data.get("facilities", {})
    estimates = {}
    for state in states:
        estimate = estimate_controlling_facility(state, approaches, frequencies, settings)
        estimate["state"] = {key: state.get(key) for key in
            ("icao24", "callsign", "altitude_ft", "baro_altitude_m", "geo_altitude_m",
             "velocity_mps", "track_deg", "vertical_rate_mps", "on_ground", "age_s")}
        estimates[state["icao24"]] = estimate
    counts: dict[str, int] = {}
    for value in estimates.values():
        facility = value["facility_type"]
        counts[facility] = counts.get(facility, 0) + 1
    return {"estimates": estimates, "counts": counts,
            "note": "Estimate. Not a radio observation."}


def load_frequencies(*, airport_ident: str, data_path: Path, override_path: Path | None = None,
                     types: tuple[str, ...] = FREQUENCY_TYPES) -> list[dict]:
    """Read only a selected airport from the supplied CSV; no network requests."""
    overrides = []
    if override_path and override_path.exists():
        overrides = (yaml.safe_load(override_path.read_text(encoding="utf-8")) or {}).get("overrides", [])
    items = []
    with data_path.open(newline="", encoding="utf-8-sig") as stream:
        for original in csv.DictReader(stream):
            if original.get("airport_ident", "").upper() != airport_ident.upper():
                continue
            if original.get("type") not in types:
                continue
            item = dict(original)
            try:
                item["frequency_mhz"] = float(item["frequency_mhz"])
            except (TypeError, ValueError):
                item["frequency_mhz"] = None
            for override in overrides:
                matches = (override.get("airport_ident", "").upper() == airport_ident.upper()
                           and override.get("type") == item.get("type")
                           and float(override.get("frequency_mhz", -1)) == item["frequency_mhz"]
                           and override.get("description") == item.get("description"))
                if matches:
                    item.update(override.get("values", {}))
                    logger.warning("Applying frequency override %s %s MHz: %s (%s)", airport_ident,
                                   item["frequency_mhz"], override.get("description"), override.get("reason"))
            items.append(item)
    return items


def estimate_facility_roles(frequencies: list[Mapping], departure_fallback_type: str = "APP") -> dict:
    """Estimate role sources; a fallback is not an assigned or verified frequency."""
    by_type: dict[str, list[dict]] = {}
    for row in frequencies:
        by_type.setdefault(str(row["type"]), []).append(dict(row))
    departure = by_type.get("DEP")
    fallback = False
    source_type = "DEP"
    if not departure:
        source_type = departure_fallback_type
        departure = by_type.get(source_type, [])
        fallback = bool(departure)
    clearance = by_type.get("CLR")
    clearance_source = "CLR"
    clearance_fallback = False
    if not clearance:
        clearance_source = departure_fallback_type
        clearance = by_type.get(clearance_source, [])
        clearance_fallback = bool(clearance)
    return {"departure_estimate": {"source_type": source_type if departure else None,
                                  "frequencies": departure or [], "is_fallback": fallback,
                                  "note": (f"No DEP entry; {source_type} is shown only as a facility estimate. "
                                           "Verify against the official AIP." if fallback else
                                           "No departure-related entry available; verify against the official AIP.")},
            "clearance_estimate": {"source_type": clearance_source if clearance else None,
                                   "frequencies": clearance or [], "is_fallback": clearance_fallback,
                                   "note": (f"No CLR entry; {clearance_source} is shown only as a facility estimate. "
                                            "Verify against the official AIP." if clearance_fallback else
                                            "No clearance entry available; verify against the official AIP.")},
            "facilities": by_type,
            "verification": "All OurAirports frequency records and role estimates must be verified against the official AIP."}
