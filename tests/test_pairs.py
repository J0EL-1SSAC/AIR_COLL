from pyproj import Transformer

from backend.airwatch.pairs import compute_pair_cpas


CPA = {
    "lookahead_s": 60, "horizontal_cutoff_nm": 12, "altitude_band_ft": 5000,
    "relative_speed_epsilon_mps": 0.1, "position_epsilon_m": 0.01,
    "altitude_basis_policy": "common_geometric_then_barometric",
    "minimum_data_quality": "not_low_quality", "uncertainty_combination": "root_sum_square",
}
PREDICTION = {
    "max_data_age_s": 45,
    "uncertainty": {"base_position_error_m": 30, "speed_error_mps": 1.5, "age_error_m_per_s": 0.5},
}
INVERSE = Transformer.from_crs(
    "+proj=aeqd +lat_0=12.9941 +lon_0=80.1709 +datum=WGS84 +units=m +no_defs",
    "EPSG:4326", always_xy=True,
)


def aircraft(icao24, *, x=0, y=0, vx=0, vy=0, on_ground=False, age=1,
             flags=(), geo=1000, baro=1200, speed=50, track=90):
    return {"icao24": icao24, "callsign": f"C{icao24}", "x_m": x, "y_m": y,
            "analysis_x_m": x, "analysis_y_m": y, "vx_mps": vx, "vy_mps": vy,
            "velocity_mps": speed, "track_deg": track, "on_ground": on_ground,
            "age_s": age, "quality_flags": list(flags), "geo_altitude_m": geo,
            "baro_altitude_m": baro, "vertical_rate_mps": 0}


def pairs(states):
    return compute_pair_cpas(states, airport_radius_nm=40, cpa_settings=CPA,
                             prediction_settings=PREDICTION, inverse_transformer=INVERSE)


def test_pair_record_is_canonical_and_contains_required_metrics():
    result = pairs([
        aircraft("bbbbbb", x=500, vx=-10),
        aircraft("aaaaaa", x=-500, vx=10),
    ])
    assert result["pairs_considered"] == 1
    assert result["pairs_returned"] == 1
    row = result["pairs"][0]
    assert row["pair_key"] == "aaaaaa|bbbbbb"
    assert row["t_cpa_s"] == 50
    assert row["converging"] is True
    assert row["altitude_basis"] == "geometric"
    assert row["combined_uncertainty_m"] > row["uncertainty_a_m"]
    assert 12 < row["cpa_position"]["aircraft_a"]["latitude"] < 14
    assert row["cpa_position"]["aircraft_a"]["longitude"] > 79


def test_prefilter_reports_ground_stale_distance_and_quality_counts():
    result = pairs([
        aircraft("a", x=0, vx=5),
        aircraft("b", x=100, vx=-5),
        aircraft("c", x=200, vx=-5, on_ground=True),
        aircraft("d", x=300, vx=-5, age=60),
        aircraft("e", x=400, vx=-5, flags=["LOW_QUALITY"]),
        aircraft("f", x=30_000, vx=-5),
    ])
    assert result["pairs_considered"] == 15
    assert result["filtered_by"]["not_airborne"] > 0
    assert result["filtered_by"]["stale_or_low_quality"] > 0
    assert result["filtered_by"]["current_distance"] > 0
    assert sum(result["filtered_by"].values()) + result["pairs_returned"] == result["pairs_considered"]


def test_altitude_band_filters_when_a_common_basis_is_available():
    result = pairs([aircraft("a", x=0, vx=10, geo=100),
                    aircraft("b", x=100, vx=-10, geo=2000)])
    assert result["filtered_by"]["altitude_band"] == 1
    assert result["pairs"] == []


def test_mixed_altitude_sources_remain_in_pair_with_null_vertical_separation():
    result = pairs([aircraft("a", x=0, vx=10, geo=100, baro=None),
                    aircraft("b", x=100, vx=-10, geo=None, baro=110)])
    assert result["pairs_returned"] == 1
    pair = result["pairs"][0]
    assert pair["altitude_basis"] == "unavailable"
    assert pair["v_sep_cpa_m"] is None
    assert pair["current_v_sep_m"] is None


def test_spatial_grid_counts_far_candidates_without_returning_them():
    result = pairs([aircraft("a", x=0), aircraft("b", x=1_000), aircraft("c", x=30_000)])
    assert result["pairs_considered"] == 3
    assert result["filtered_by"]["current_distance"] > 0
