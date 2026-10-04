"""Offline OurAirports frequency reference and explicitly labeled role estimates."""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Mapping

import yaml

logger = logging.getLogger(__name__)
FREQUENCY_TYPES = ("ACC", "APP", "ATIS", "GCA", "GND", "TWR")


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
