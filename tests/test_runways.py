import logging
import json

import pytest

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
    assert any("magnetic_variation_deg is 0.0" in item for item in runway.warnings)


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
    runway = load_runways(airport_ident="TEST", latitude=0, longitude=0, settings=settings)[0]
    assert runway.end_a.magnetic_heading_deg == pytest.approx(355, abs=0.1)
