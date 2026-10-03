"""Pure runway and ENU geometry helpers."""
from __future__ import annotations

import math
from dataclasses import dataclass

from pyproj import Transformer
from shapely.geometry import Point, Polygon

NM_TO_M = 1852.0


@dataclass(frozen=True)
class RunwayEndGeometry:
    identifier: str
    threshold_x_m: float
    threshold_y_m: float
    true_heading_deg: float
    displaced_threshold_m: float = 0.0


def heading_difference(first_deg: float, second_deg: float) -> float:
    """Smallest unsigned angular difference in degrees."""
    return abs((float(first_deg) - float(second_deg) + 180.0) % 360.0 - 180.0)


def distance_to_threshold(x_m: float, y_m: float, threshold_x_m: float, threshold_y_m: float) -> float:
    return math.hypot(float(x_m) - float(threshold_x_m), float(y_m) - float(threshold_y_m))


def _heading_unit(heading_deg: float) -> tuple[float, float]:
    angle = math.radians(float(heading_deg))
    return math.sin(angle), math.cos(angle)


def along_track_distance(x_m: float, y_m: float, threshold_x_m: float, threshold_y_m: float,
                         true_heading_deg: float) -> float:
    """Signed distance from threshold; positive is on the approach side."""
    east, north = _heading_unit(true_heading_deg)
    return -((float(x_m) - threshold_x_m) * east + (float(y_m) - threshold_y_m) * north)


def cross_track_error(x_m: float, y_m: float, threshold_x_m: float, threshold_y_m: float,
                      true_heading_deg: float) -> float:
    """Signed perpendicular distance; positive is to the right of runway heading."""
    east, north = _heading_unit(true_heading_deg)
    dx, dy = float(x_m) - threshold_x_m, float(y_m) - threshold_y_m
    return dx * north - dy * east


def rectangle_between_ends(start: tuple[float, float], end: tuple[float, float], width_m: float,
                           *, longitudinal_extension_m: float = 0.0,
                           lateral_extension_m: float = 0.0) -> Polygon:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 0:
        raise ValueError("runway endpoints must be distinct")
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    half_width = float(width_m) / 2.0 + float(lateral_extension_m)
    start_x, start_y = start[0] - ux * longitudinal_extension_m, start[1] - uy * longitudinal_extension_m
    end_x, end_y = end[0] + ux * longitudinal_extension_m, end[1] + uy * longitudinal_extension_m
    return Polygon([
        (start_x + px * half_width, start_y + py * half_width),
        (end_x + px * half_width, end_y + py * half_width),
        (end_x - px * half_width, end_y - py * half_width),
        (start_x - px * half_width, start_y - py * half_width),
    ])


def approach_corridor(end: RunwayEndGeometry, *, length_m: float, half_width_threshold_m: float,
                      half_width_far_m: float) -> Polygon:
    """Trapezoid extending outward from the displaced threshold on approach."""
    along = _heading_unit(end.true_heading_deg)
    right = (along[1], -along[0])
    threshold_x = end.threshold_x_m - along[0] * end.displaced_threshold_m
    threshold_y = end.threshold_y_m - along[1] * end.displaced_threshold_m
    far_x, far_y = threshold_x - along[0] * length_m, threshold_y - along[1] * length_m
    return Polygon([
        (threshold_x + right[0] * half_width_threshold_m, threshold_y + right[1] * half_width_threshold_m),
        (threshold_x - right[0] * half_width_threshold_m, threshold_y - right[1] * half_width_threshold_m),
        (far_x - right[0] * half_width_far_m, far_y - right[1] * half_width_far_m),
        (far_x + right[0] * half_width_far_m, far_y + right[1] * half_width_far_m),
    ])


def point_in_core(x_m: float, y_m: float, polygon: Polygon) -> bool:
    return polygon.covers(Point(float(x_m), float(y_m)))


def point_in_buffer(x_m: float, y_m: float, polygon: Polygon) -> bool:
    return polygon.covers(Point(float(x_m), float(y_m)))


def point_in_corridor(x_m: float, y_m: float, polygon: Polygon, *, altitude_m: float | None = None,
                      altitude_ceiling_m: float | None = None) -> bool:
    if altitude_m is not None and altitude_ceiling_m is not None and altitude_m > altitude_ceiling_m:
        return False
    return polygon.covers(Point(float(x_m), float(y_m)))


def nearest_runway_end(x_m: float, y_m: float, ends: list[RunwayEndGeometry]) -> tuple[RunwayEndGeometry, float]:
    if not ends:
        raise ValueError("at least one runway end is required")
    end = min(ends, key=lambda item: distance_to_threshold(x_m, y_m, item.threshold_x_m, item.threshold_y_m))
    return end, distance_to_threshold(x_m, y_m, end.threshold_x_m, end.threshold_y_m)


def runway_for_point(x_m: float, y_m: float, runways: list[dict], *, include_buffer: bool = False) -> dict | None:
    key = "buffer_polygon" if include_buffer else "core_polygon"
    for runway in runways:
        if runway[key].covers(Point(float(x_m), float(y_m))):
            return runway
    return None


def local_transformers(latitude: float, longitude: float) -> tuple[Transformer, Transformer]:
    local = f"+proj=aeqd +lat_0={latitude} +lon_0={longitude} +datum=WGS84 +units=m +no_defs"
    return (Transformer.from_crs("EPSG:4326", local, always_xy=True),
            Transformer.from_crs(local, "EPSG:4326", always_xy=True))
