import math

import pytest
from pyproj import Transformer

from backend.airwatch.prediction import ConstantVelocityPredictor, track_to_velocity_components


class FixedClock:
    def __init__(self, now):
        self.timestamp = now

    def now(self):
        return self.timestamp


SETTINGS = {
    "ground_elevation_m": 16.4592,
    "max_data_age_s": 45,
    "skip_on_ground": True,
    "skip_low_quality": True,
    "skip_stale": True,
    "uncertainty": {
        "base_position_error_m": 30,
        "speed_error_mps": 1.5,
        "age_error_m_per_s": 0.5,
    },
}
LOCAL = "+proj=aeqd +lat_0=0 +lon_0=0 +datum=WGS84 +units=m +no_defs"


def predictor(now=1000):
    return ConstantVelocityPredictor(
        clock=FixedClock(now),
        inverse_transformer=Transformer.from_crs(LOCAL, "EPSG:4326", always_xy=True),
        settings=SETTINGS,
    )


def state(track=0, speed=10, *, timestamp=1000, x=0, y=0, altitude=100,
          vertical_rate=0, on_ground=False, flags=()):
    vx, vy = track_to_velocity_components(speed, track)
    return {
        "icao24": "test0001", "x_m": x, "y_m": y, "vx_mps": vx, "vy_mps": vy,
        "velocity_mps": speed, "track_deg": track, "position_timestamp": timestamp,
        "baro_altitude_m": altitude, "geo_altitude_m": altitude,
        "vertical_rate_mps": vertical_rate, "on_ground": on_ground,
        "quality_flags": list(flags),
    }


@pytest.mark.parametrize(
    ("track", "speed", "expected_x", "expected_y"),
    [(0, 10, 0, 100), (90, 10, 100, 0), (180, 10, 0, -100),
     (270, 10, -100, 0), (45, math.sqrt(2), 10, 10)],
)
def test_true_track_maps_to_expected_enu_direction(track, speed, expected_x, expected_y):
    vx, vy = track_to_velocity_components(speed, track)
    assert vx == pytest.approx(expected_x / 10)
    assert vy == pytest.approx(expected_y / 10)
    point = predictor().predict(state(track, speed), [10])["points"][0]
    assert point["x_m"] == pytest.approx(expected_x)
    assert point["y_m"] == pytest.approx(expected_y)


def test_zero_speed_keeps_position_fixed():
    result = predictor().predict(state(track=225, speed=0), [5, 60])
    assert [(p["x_m"], p["y_m"]) for p in result["points"]] == pytest.approx([(0, 0), (0, 0)])


def test_age_compensation_advances_position_before_horizon():
    result = predictor(now=1010).predict(state(track=90, speed=2, timestamp=1000), [5])
    assert result["points"][0]["x_m"] == pytest.approx(30)
    assert result["age_s"] == pytest.approx(10)


def test_predictor_uses_state_manager_analysis_compensated_position():
    item = state(track=90, speed=2, timestamp=1000)
    item.update(analysis_x_m=22, analysis_y_m=7)
    result = predictor(now=1010).predict(item, [5])
    assert result["origin"]["x_m"] == pytest.approx(22)
    assert result["origin"]["y_m"] == pytest.approx(7)


def test_vertical_prediction_climbs_and_descends_but_clamps_at_ground():
    climb = predictor().predict(state(altitude=100, vertical_rate=2), [5])
    descent = predictor().predict(state(altitude=25, vertical_rate=-2), [5])
    assert climb["points"][0]["altitude_m"] == pytest.approx(110)
    assert descent["points"][0]["altitude_m"] == SETTINGS["ground_elevation_m"]


def test_uncertainty_grows_with_horizon_and_source_age():
    result = predictor(now=1010).predict(state(timestamp=1000), [5, 20])
    short, long = result["points"]
    assert short["uncertainty_radius_m"] == pytest.approx(42.5)
    assert long["uncertainty_radius_m"] == pytest.approx(65)
    assert long["uncertainty_radius_m"] > short["uncertainty_radius_m"]


@pytest.mark.parametrize(
    ("updates", "reason"),
    [({"on_ground": True}, "ON_GROUND"),
     ({"quality_flags": ["LOW_QUALITY"]}, "LOW_QUALITY"),
     ({"position_timestamp": 900}, "STALE"),
     ({"track_deg": None}, "MISSING_TRACK"),
     ({"velocity_mps": None}, "MISSING_VELOCITY")],
)
def test_unpredictable_aircraft_return_a_reason_code(updates, reason):
    item = state()
    item.update(updates)
    result = predictor().predict(item, [5])
    assert result["status"] == "SKIPPED"
    assert result["reason_code"] == reason
    assert result["points"] == []


def test_predicted_points_include_geographic_coordinates():
    point = predictor().predict(state(track=0, speed=100), [10])["points"][0]
    assert point["latitude"] > 0
    assert point["longitude"] == pytest.approx(0, abs=1e-8)
