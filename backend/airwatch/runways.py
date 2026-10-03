"""Load and validate OurAirports runway data; no network access is performed."""
from __future__ import annotations

import csv
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from pyproj import Geod
from shapely.geometry import mapping
from shapely.ops import transform as transform_geometry

from .geometry import (NM_TO_M, RunwayEndGeometry, approach_corridor, heading_difference,
                       local_transformers, rectangle_between_ends)

logger = logging.getLogger(__name__)
GEOD = Geod(ellps="WGS84")
FT_TO_M = 0.3048


class RunwayDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunwayEnd:
    identifier: str
    latitude: float
    longitude: float
    elevation_m: float | None
    displaced_threshold_m: float
    true_heading_deg: float
    magnetic_heading_deg: float
    magnetic_designator_heading_deg: float | None
    x_m: float
    y_m: float
    corridor_polygon: Any
    altitude_ceiling_m: float

    def geometry_end(self) -> RunwayEndGeometry:
        return RunwayEndGeometry(self.identifier, self.x_m, self.y_m, self.true_heading_deg,
                                 self.displaced_threshold_m)


@dataclass(frozen=True)
class Runway:
    identifier: str
    pair_identifier: str
    airport_ident: str
    length_m: float
    width_m: float
    source_length_m: float | None
    surface: str | None
    lighted: bool | None
    closed: bool
    end_a: RunwayEnd
    end_b: RunwayEnd
    centerline_extension_m: float
    core_polygon: Any
    buffer_polygon: Any
    warnings: tuple[str, ...]

    def as_dict(self, *, inverse_transformer=None) -> dict:
        def end_dict(end: RunwayEnd, opposite: RunwayEnd) -> dict:
            result = {"identifier": end.identifier, "latitude": end.latitude, "longitude": end.longitude,
                      "elevation_m": end.elevation_m, "displaced_threshold_m": end.displaced_threshold_m,
                      "true_heading_deg": end.true_heading_deg,
                      "magnetic_heading_deg": end.magnetic_heading_deg,
                      "magnetic_designator_heading_deg": end.magnetic_designator_heading_deg,
                      "opposite_threshold": {"latitude": opposite.latitude, "longitude": opposite.longitude,
                                              "elevation_m": opposite.elevation_m}}
            result.update({"x_m": end.x_m, "y_m": end.y_m,
                           "corridor_geojson": _geometry_geojson(end.corridor_polygon, inverse_transformer)})
            return result
        from shapely.geometry import LineString
        ea_u = (math.sin(math.radians(self.end_a.true_heading_deg)), math.cos(math.radians(self.end_a.true_heading_deg)))
        eb_u = (math.sin(math.radians(self.end_b.true_heading_deg)), math.cos(math.radians(self.end_b.true_heading_deg)))
        extension_m = self.centerline_extension_m
        centerline = LineString([(self.end_a.x_m - ea_u[0] * extension_m, self.end_a.y_m - ea_u[1] * extension_m),
                                 (self.end_a.x_m, self.end_a.y_m), (self.end_b.x_m, self.end_b.y_m),
                                 (self.end_b.x_m + eb_u[0] * extension_m, self.end_b.y_m + eb_u[1] * extension_m)])
        return {"identifier": self.identifier, "pair_identifier": self.pair_identifier,
                "airport_ident": self.airport_ident, "length_m": self.length_m, "width_m": self.width_m,
                "source_length_m": self.source_length_m, "surface": self.surface, "lighted": self.lighted,
                "closed": self.closed, "end_a": end_dict(self.end_a, self.end_b), "end_b": end_dict(self.end_b, self.end_a),
                "centerline_geojson": _geometry_geojson(centerline, inverse_transformer),
                "core_geojson": _geometry_geojson(self.core_polygon, inverse_transformer),
                "buffer_geojson": _geometry_geojson(self.buffer_polygon, inverse_transformer),
                "warnings": list(self.warnings)}


def _geometry_geojson(geometry, inverse_transformer) -> dict | None:
    if geometry is None:
        return None
    if inverse_transformer is not None:
        geometry = transform_geometry(lambda x, y, z=None: inverse_transformer.transform(x, y), geometry)
    return {"type": "Feature", "geometry": mapping(geometry), "properties": {}}


def _float(row: Mapping[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None or str(value).strip() == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _bool(row: Mapping[str, Any], key: str) -> bool | None:
    value = row.get(key)
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _designator_heading(identifier: str) -> float | None:
    digits = "".join(character for character in (identifier or "") if character.isdigit())
    if not digits:
        return None
    value = int(digits) * 10.0
    return 360.0 if value == 0 else value


def _load_overrides(path: Path | None) -> dict[tuple[str, str, str], dict]:
    if path is None or not path.exists():
        return {}
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream) or {}
    overrides = document.get("overrides", [])
    result = {}
    for override in overrides:
        key = (str(override["airport_ident"]).upper(), str(override["le_ident"]).upper(),
               str(override["he_ident"]).upper())
        values = dict(override.get("values", {}))
        if not values:
            values = {name: value for name, value in override.items()
                      if name not in {"airport_ident", "le_ident", "he_ident", "reason"}}
        logger.warning("Applying runway override %s/%s-%s: %s (%s)", *key,
                       sorted(values), override.get("reason", "no reason specified"))
        result[key] = values
    return result


def load_runways(*, airport_ident: str, latitude: float, longitude: float, settings: Mapping,
                 data_path: Path | None = None, override_path: Path | None = None) -> list[Runway]:
    data_path = data_path or Path(settings["data_file"])
    override_path = override_path or Path(settings["override_file"])
    if not data_path.exists():
        raise RunwayDataError(f"Runway data file not found: {data_path}. Download OurAirports runways.csv manually and place it at this path; the application does not download it.")
    overrides = _load_overrides(override_path)
    forward, _inverse = local_transformers(latitude, longitude)
    variation = float(settings["magnetic_variation_deg"])
    include_closed = bool(settings["include_closed"])
    runway_list = []
    with data_path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"airport_ident", "le_ident", "he_ident", "le_latitude_deg", "le_longitude_deg",
                    "he_latitude_deg", "he_longitude_deg"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise RunwayDataError(f"{data_path} is missing OurAirports columns: {', '.join(sorted(missing))}")
        for original in reader:
            if str(original.get("airport_ident", "")).upper() != airport_ident.upper():
                continue
            row = dict(original)
            override = overrides.get((airport_ident.upper(), str(row.get("le_ident", "")).upper(),
                                      str(row.get("he_ident", "")).upper()))
            if override:
                row.update(override)
            closed = _bool(row, "closed") is True
            if closed and not include_closed:
                continue
            la_lat, la_lon = _float(row, "le_latitude_deg"), _float(row, "le_longitude_deg")
            hb_lat, hb_lon = _float(row, "he_latitude_deg"), _float(row, "he_longitude_deg")
            if None in (la_lat, la_lon, hb_lat, hb_lon):
                logger.warning("Skipping runway with missing endpoint coordinates: %s/%s-%s", airport_ident,
                               row.get("le_ident"), row.get("he_ident"))
                continue
            ax, ay = forward.transform(la_lon, la_lat)
            bx, by = forward.transform(hb_lon, hb_lat)
            az_ab, _az_ba, geod_length = GEOD.inv(la_lon, la_lat, hb_lon, hb_lat)
            heading_ab = az_ab % 360.0
            heading_ba = (heading_ab + 180.0) % 360.0
            width_m = (_float(row, "width_ft") or float(settings["default_width_m"]) / FT_TO_M) * FT_TO_M
            length_value = _float(row, "length_ft")
            source_length_m = None if length_value is None else length_value * FT_TO_M
            length_m = source_length_m if source_length_m and source_length_m > 0 else geod_length
            end_a_id, end_b_id = str(row.get("le_ident", "")), str(row.get("he_ident", ""))
            corridor_length = float(settings["approach_length_nm"]) * NM_TO_M
            common = {"length_m": corridor_length,
                      "half_width_threshold_m": float(settings["corridor_half_width_threshold_m"]),
                      "half_width_far_m": float(settings["corridor_half_width_far_m"])}
            designator_a, designator_b = _designator_heading(end_a_id), _designator_heading(end_b_id)
            end_a = RunwayEnd(end_a_id, la_lat, la_lon,
                              None if _float(row, "le_elevation_ft") is None else _float(row, "le_elevation_ft") * FT_TO_M,
                              (_float(row, "le_displaced_threshold_ft") or 0.0) * FT_TO_M,
                              heading_ab, (heading_ab - variation) % 360.0, designator_a, ax, ay,
                              approach_corridor(RunwayEndGeometry(end_a_id, ax, ay, heading_ab,
                                  (_float(row, "le_displaced_threshold_ft") or 0.0) * FT_TO_M), **common),
                              float(settings["approach_altitude_ceiling_m"]))
            end_b = RunwayEnd(end_b_id, hb_lat, hb_lon,
                              None if _float(row, "he_elevation_ft") is None else _float(row, "he_elevation_ft") * FT_TO_M,
                              (_float(row, "he_displaced_threshold_ft") or 0.0) * FT_TO_M,
                              heading_ba, (heading_ba - variation) % 360.0, designator_b, bx, by,
                              approach_corridor(RunwayEndGeometry(end_b_id, bx, by, heading_ba,
                                  (_float(row, "he_displaced_threshold_ft") or 0.0) * FT_TO_M), **common),
                              float(settings["approach_altitude_ceiling_m"]))
            warnings = []
            if variation == 0.0:
                warnings.append("magnetic_variation_deg is 0.0; confirm the current VOMM variation for the source/chart effective date")
            threshold_limit = float(settings["max_threshold_distance_from_airport_m"])
            for end in (end_a, end_b):
                airport_distance = math.hypot(end.x_m, end.y_m)
                if airport_distance > threshold_limit:
                    warnings.append(f"{end.identifier} threshold is {airport_distance:.1f} m from airport reference point (limit {threshold_limit:.1f} m)")
                if end.magnetic_designator_heading_deg is not None:
                    # East variation is positive: true heading = magnetic heading + variation.
                    expected_true = (end.magnetic_designator_heading_deg + variation) % 360.0
                    error = heading_difference(end.true_heading_deg, expected_true)
                    if error > float(settings["heading_tolerance_deg"]):
                        warnings.append(f"{end.identifier} true heading differs from designator plus magnetic variation by {error:.1f}°")
            length_error = abs(geod_length - (source_length_m if source_length_m is not None else geod_length))
            if length_error > float(settings["length_tolerance_m"]):
                warnings.append(f"coordinate length {geod_length:.1f} m differs from source length {(source_length_m or 0):.1f} m by {length_error:.1f} m")
            for warning in warnings:
                logger.warning("Runway %s/%s-%s: %s", airport_ident, end_a_id, end_b_id, warning)
            core = rectangle_between_ends((ax, ay), (bx, by), width_m)
            buffer = rectangle_between_ends((ax, ay), (bx, by), width_m,
                                            longitudinal_extension_m=float(settings["buffer_longitudinal_m"]),
                                            lateral_extension_m=float(settings["buffer_lateral_m"]))
            pair_id = f"{end_a_id}/{end_b_id}"
            runway_list.append(Runway(pair_id, pair_id, airport_ident, length_m, width_m, source_length_m,
                                      row.get("surface") or None, _bool(row, "lighted"), closed,
                                      end_a, end_b, corridor_length, core, buffer, tuple(warnings)))
    if not runway_list:
        raise RunwayDataError(f"No runway rows found for airport_ident={airport_ident} in {data_path} (closed rows may be excluded).")
    return runway_list


def runway_report_rows(runways: list[Runway]) -> list[dict]:
    rows = []
    for runway in runways:
        for end, reciprocal in ((runway.end_a, runway.end_b), (runway.end_b, runway.end_a)):
            rows.append({"runway": runway.pair_identifier, "identifier": end.identifier,
                         "latitude": end.latitude, "longitude": end.longitude,
                         "elevation_m": end.elevation_m, "length_m": runway.length_m,
                         "width_m": runway.width_m, "true_heading_deg": end.true_heading_deg,
                         "magnetic_heading_deg": end.magnetic_heading_deg,
                         "magnetic_designator_heading_deg": end.magnetic_designator_heading_deg,
                         "reciprocal_identifier": reciprocal.identifier, "warnings": runway.warnings})
    return rows
