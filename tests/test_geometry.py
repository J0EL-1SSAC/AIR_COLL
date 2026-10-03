import pytest
from shapely.geometry import Point

from backend.airwatch.geometry import (
    RunwayEndGeometry, along_track_distance, approach_corridor, cross_track_error,
    distance_to_threshold, heading_difference, point_in_buffer, point_in_core,
    point_in_corridor, rectangle_between_ends, runway_for_point,
)


def test_heading_difference_wraps_across_zero():
    assert heading_difference(350, 10) == pytest.approx(20)


def test_cross_track_sign_is_positive_to_right_of_heading():
    assert cross_track_error(10, 0, 0, 0, 0) == pytest.approx(10)  # east is right of north
    assert cross_track_error(-10, 0, 0, 0, 0) == pytest.approx(-10)


def test_along_track_sign_threshold_and_distance():
    assert along_track_distance(0, -20, 0, 0, 0) == pytest.approx(20)
    assert along_track_distance(0, 20, 0, 0, 0) == pytest.approx(-20)
    assert along_track_distance(0, 0, 0, 0, 0) == 0
    assert distance_to_threshold(3, 4, 0, 0) == pytest.approx(5)


def test_core_buffer_and_runway_lookup():
    core = rectangle_between_ends((0, 0), (0, 100), 20)
    buffer = rectangle_between_ends((0, 0), (0, 100), 20,
                                    longitudinal_extension_m=10, lateral_extension_m=5)
    assert point_in_core(0, 50, core)
    assert not point_in_core(13, 50, core)
    assert point_in_buffer(14, 50, buffer)
    assert not point_in_buffer(30, 50, buffer)
    runway = {"core_polygon": core, "buffer_polygon": buffer}
    assert runway_for_point(0, 50, [runway]) is runway
    assert runway_for_point(14, 50, [runway], include_buffer=True) is runway
    assert runway_for_point(30, 50, [runway]) is None


def test_approach_corridors_face_reciprocal_directions_and_shift_for_displacement():
    south = RunwayEndGeometry("18", 0, 0, 0, displaced_threshold_m=10)
    north = RunwayEndGeometry("36", 0, 100, 180)
    south_corridor = approach_corridor(south, length_m=100, half_width_threshold_m=10, half_width_far_m=20)
    north_corridor = approach_corridor(north, length_m=100, half_width_threshold_m=10, half_width_far_m=20)
    assert point_in_corridor(0, -20, south_corridor)
    assert not point_in_corridor(0, 50, south_corridor)
    assert point_in_corridor(0, 120, north_corridor)
    assert not point_in_corridor(0, 50, north_corridor)
    assert not point_in_corridor(0, 120, north_corridor, altitude_m=1600, altitude_ceiling_m=1500)
    # A displacement moves the approach-side corridor origin ten meters outward.
    assert south_corridor.covers(Point(0, -10))
