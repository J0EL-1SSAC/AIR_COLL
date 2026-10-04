import logging
import json
import math

import pytest
from shapely.geometry import LineString, Point

from backend.airwatch.runways import RunwayDataError, load_runways
from backend.airwatch.geometry import local_transformers


HEADERS = "airport_ident,le_ident,he_ident,length_ft,width_ft,surface,lighted,closed,le_latitude_deg,le_longitude_deg,le_elevation_ft,le_displaced_threshold_ft,he_latitude_deg,he_longitude_deg,he_elevation_ft,he_displaced_threshold_ft\n"


def _settings(path, overrides=None):
    return {"data_file": path, "override_file": overrides or path.parent / "missing.yaml",
            "include_closed": False, "default_width_m": 40, "magnetic_variation_deg": 0,
            "heading_tolerance_deg": 12, "max_threshold_distance_from_airport_m": 2000,
            "length_tolerance_m": 20, "buffer_lateral_m": 20, "buffer_longitudinal_m": 30,
            "approach_length_nm": 2, "corridor_half_width_threshold_m": 100,
            "corridor_half_width_far_m": 500, "approach_altitude_ceiling_m": 500}


def test_loads_end_headings_length_default_width_and_warnings(tmp_path):
    source = tmp_path / "runways.csv"
    source.write_text(HEADERS + "TEST,36,18,3650,,ASP,1,0,0,0,0,100,0.01,0,0,0\n", encoding="utf-8")
    runways = load_runways(airport_ident="TEST", latitude=0, longitude=0,
                           settings=_settings(source))
    assert len(runways) == 1
    runway = runways[0]
    assert runway.pair_identifier == "36/18"
    assert runway.width_m == pytest.approx(40)
    assert runway.end_a.true_heading_deg == pytest.approx(0, abs=0.1)
    assert runway.end_a.magnetic_designator_heading_deg == 360
    assert runway.end_b.true_heading_deg == pytest.approx(180, abs=0.1)
    assert runway.end_a.displaced_threshold_m == pytest.approx(30.48)
    assert runway.length_m == pytest.approx(3650 * 0.3048)
    assert any("UNCONFIRMED" in item for item in runway.warnings)


def test_local_vomm_csv_reference_geometry_and_displaced_landing_threshold():
    from pathlib import Path
    import yaml
    from backend.airwatch.geometry import heading_difference
    from backend.airwatch.geometry import along_track_distance

    root = Path(__file__).resolve().parents[1]
    source = root / "data/runways.csv"
    if not source.exists():
        pytest.skip("manual OurAirports runways.csv is not installed")
    config = yaml.safe_load((root / "config.yaml").read_text())
    settings = dict(config["runways"])
    settings["data_file"] = source
    settings["override_file"] = root / settings["override_file"]
    runways = load_runways(airport_ident="VOMM", latitude=config["airport"]["latitude"],
                           longitude=config["airport"]["longitude"], settings=settings)
    pairs = {runway.pair_identifier: runway for runway in runways}
    assert set(pairs) == {"07/25", "12/30"}
    assert pairs["07/25"].length_m / 0.3048 == pytest.approx(12001, abs=1)
    assert pairs["07/25"].width_m / 0.3048 == pytest.approx(148, abs=1)
    assert pairs["07/25"].surface == "ASP"
    assert pairs["12/30"].length_m / 0.3048 == pytest.approx(6708, abs=1)
    assert pairs["12/30"].width_m / 0.3048 == pytest.approx(148, abs=1)
    assert pairs["12/30"].surface == "PEM"
    expected_headings = {"07": 68.9, "25": 248.9, "12": 117.4, "30": 297.4}
    for runway in pairs.values():
        for end in (runway.end_a, runway.end_b):
            assert end.true_heading_deg == pytest.approx(expected_headings[end.identifier], abs=0.2)
    end30 = pairs["12/30"].end_b
    assert end30.identifier == "30"
    assert end30.displaced_threshold_m / 0.3048 == pytest.approx(787, abs=1)
    assert end30.landing_threshold_x_m is not None
    assert end30.landing_threshold_y_m is not None
    assert ((end30.landing_threshold_x_m - end30.x_m) ** 2 +
            (end30.landing_threshold_y_m - end30.y_m) ** 2) ** 0.5 == pytest.approx(787 * 0.3048, abs=0.02)
    assert along_track_distance(end30.landing_threshold_x_m, end30.landing_threshold_y_m,
                                 end30.landing_threshold_x_m, end30.landing_threshold_y_m,
                                 end30.true_heading_deg) == pytest.approx(0)
    assert along_track_distance(end30.x_m, end30.y_m,
                                 end30.landing_threshold_x_m, end30.landing_threshold_y_m,
                                 end30.true_heading_deg) == pytest.approx(-787 * 0.3048, abs=0.03)
    approach_x = end30.landing_threshold_x_m - 1000 * math.sin(math.radians(end30.true_heading_deg))
    approach_y = end30.landing_threshold_y_m - 1000 * math.cos(math.radians(end30.true_heading_deg))
    assert end30.corridor_polygon.covers(Point(approach_x, approach_y))
    for (first, second), overlap_expected in (((pairs["07/25"].end_b, pairs["12/30"].end_b), True),
                                               ((pairs["07/25"].end_a, pairs["12/30"].end_a), False)):
        assert heading_difference(first.true_heading_deg, second.true_heading_deg) > 2 * config["approach"]["max_heading_diff_deg"]
        assert first.corridor_polygon.intersects(second.corridor_polygon) is overlap_expected
    main, cross = pairs["07/25"], pairs["12/30"]
    assert main.buffer_polygon.intersects(cross.buffer_polygon)
    main_center = LineString([(main.end_a.x_m, main.end_a.y_m), (main.end_b.x_m, main.end_b.y_m)])
    cross_center = LineString([(cross.end_a.x_m, cross.end_a.y_m), (cross.end_b.x_m, cross.end_b.y_m)])
    intersection = main_center.intersection(cross_center)
    assert intersection.geom_type == "Point"
    d25 = math.hypot(intersection.x-pairs["07/25"].end_b.landing_threshold_x_m,
                     intersection.y-pairs["07/25"].end_b.landing_threshold_y_m)
    d30 = math.hypot(intersection.x-end30.landing_threshold_x_m,
                     intersection.y-end30.landing_threshold_y_m)
    assert d25 == pytest.approx(467.3, abs=2)
    assert d30 == pytest.approx(498.8, abs=2)


def test_closed_runway_excluded_unless_configured(tmp_path):
    source = tmp_path / "runways.csv"
    source.write_text(HEADERS + "TEST,36,18,3650,100,ASP,0,1,0,0,0,0,0.01,0,0,0\n", encoding="utf-8")
    with pytest.raises(RunwayDataError):
        load_runways(airport_ident="TEST", latitude=0, longitude=0, settings=_settings(source))
    settings = _settings(source)
    settings["include_closed"] = True
    assert load_runways(airport_ident="TEST", latitude=0, longitude=0, settings=settings)[0].closed


def test_missing_file_fails_with_manual_download_instruction(tmp_path):
    with pytest.raises(RunwayDataError, match="(?i)download OurAirports runways.csv manually"):
        load_runways(airport_ident="TEST", latitude=0, longitude=0,
                     settings=_settings(tmp_path / "absent.csv"))


def test_override_is_applied_and_logged(tmp_path, caplog):
    source = tmp_path / "runways.csv"
    source.write_text(HEADERS + "TEST,36,18,3650,100,ASP,0,0,0,0,0,0,0.01,0,0,0\n", encoding="utf-8")
    override = tmp_path / "runways_override.yaml"
    override.write_text("overrides:\n  - airport_ident: TEST\n    le_ident: '36'\n    he_ident: '18'\n    values:\n      width_ft: 200\n    reason: chart correction\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_runways(airport_ident="TEST", latitude=0, longitude=0,
                              settings=_settings(source, override))
    assert result[0].width_m == pytest.approx(200 * 0.3048)
    assert "Applying runway override" in caplog.text


def test_geojson_output_uses_lon_lat_coordinates_and_serializes(tmp_path):
    source = tmp_path / "runways.csv"
    source.write_text(HEADERS + "TEST,36,18,3650,100,ASP,0,0,0,0,0,0,0.01,0,0,0\n", encoding="utf-8")
    runway = load_runways(airport_ident="TEST", latitude=0, longitude=0,
                          settings=_settings(source))[0]
    _forward, inverse = local_transformers(0, 0)
    value = runway.as_dict(inverse_transformer=inverse)
    assert value["core_geojson"]["type"] == "Feature"
    first_coordinate = value["core_geojson"]["geometry"]["coordinates"][0][0]
    assert len(first_coordinate) == 2  # GeoJSON is longitude, latitude
    assert value["end_a"]["opposite_threshold"]["latitude"] == pytest.approx(runway.end_b.latitude)
    json.dumps(value)


def test_positive_east_variation_subtracts_from_true_for_magnetic_heading(tmp_path):
    source = tmp_path / "runways.csv"
    source.write_text(HEADERS + "TEST,36,18,3650,100,ASP,0,0,0,0,0,0,0.01,0,0,0\n", encoding="utf-8")
    settings = _settings(source)
    settings["magnetic_variation_deg"] = 5
    settings["magnetic_variation_confirmed"] = True
    runway = load_runways(airport_ident="TEST", latitude=0, longitude=0, settings=settings)[0]
    assert runway.end_a.magnetic_heading_deg == pytest.approx(355, abs=0.1)
