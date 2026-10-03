import math

import pytest

from backend.airwatch.cpa import calculate_cpa, select_altitude_basis


PARAMS = {"lookahead_s": 60, "relative_speed_epsilon_mps": 0.01, "position_epsilon_m": 0.01}


def cpa(a, b, **kwargs):
    settings = {**PARAMS, **kwargs}
    return calculate_cpa(
        x_a_m=a[0], y_a_m=a[1], vx_a_mps=a[2], vy_a_mps=a[3],
        x_b_m=b[0], y_b_m=b[1], vx_b_mps=b[2], vy_b_mps=b[3], **settings)


def test_head_on_approach_has_hand_computed_cpa():
    result = cpa((-100, 0, 10, 0), (100, 0, -10, 0))
    assert result["t_cpa_raw_s"] == pytest.approx(10)
    assert result["t_cpa_s"] == pytest.approx(10)
    assert result["h_sep_cpa_m"] == pytest.approx(0)
    assert result["closing_speed_mps"] == pytest.approx(20)
    assert result["converging"] is True and result["cpa_in_past"] is False


def test_right_angle_crossing_meets_at_ten_seconds():
    result = cpa((-100, 0, 10, 0), (0, -100, 0, 10))
    assert result["t_cpa_s"] == pytest.approx(10)
    assert result["h_sep_cpa_m"] == pytest.approx(0)


def test_diverging_pair_reports_negative_raw_time_and_clamped_now():
    result = cpa((0, 0, -5, 0), (100, 0, 5, 0))
    assert result["t_cpa_raw_s"] == pytest.approx(-10)
    assert result["t_cpa_s"] == 0
    assert result["cpa_in_past"] is True
    assert result["converging"] is False
    assert result["closing_speed_mps"] == pytest.approx(-10)


def test_parallel_same_speed_is_cpa_now():
    result = cpa((0, 0, 8, 6), (50, 20, 8, 6))
    assert result["t_cpa_s"] == 0
    assert result["t_cpa_raw_s"] == 0
    assert result["h_sep_cpa_m"] == pytest.approx(math.hypot(50, 20))


def test_identical_velocity_components_are_guarded():
    result = cpa((0, 0, 8, 6), (50, 20, 8, 6))
    assert result["t_cpa_s"] == 0
    assert result["closing_speed_mps"] == pytest.approx(0)
    assert result["h_sep_cpa_m"] == pytest.approx(math.hypot(50, 20))


def test_stationary_aircraft_and_moving_aircraft_have_finite_cpa():
    result = cpa((0, 0, 0, 0), (100, 0, -10, 0))
    assert result["t_cpa_s"] == pytest.approx(10)
    assert result["h_sep_cpa_m"] == pytest.approx(0)


def test_identical_positions_have_finite_outputs_and_zero_closing_speed():
    result = cpa((0, 0, 1, 0), (0, 0, -1, 0))
    assert result["current_h_sep_m"] == 0
    assert result["closing_speed_mps"] == 0
    assert result["h_sep_cpa_m"] == 0
    assert all(math.isfinite(value) for value in (result["t_cpa_s"], result["h_sep_cpa_m"], result["closing_speed_mps"]))


def test_near_zero_relative_speed_and_position_are_guarded():
    result = cpa((0, 0, 1, 1), (0.001, 0, 1.001, 1), position_epsilon_m=0.01)
    assert result["t_cpa_s"] == 0
    assert result["closing_speed_mps"] == 0
    assert math.isfinite(result["h_sep_cpa_m"])


def test_vertical_separation_accounts_for_each_vertical_rate():
    result = cpa((0, 0, 10, 0), (200, 0, -10, 0), altitude_a_m=100,
                 altitude_b_m=130, vertical_rate_a_mps=5, vertical_rate_b_mps=-1,
                 altitude_basis="geometric")
    assert result["t_cpa_s"] == pytest.approx(10)
    assert result["current_v_sep_m"] == pytest.approx(30)
    assert result["v_sep_cpa_m"] == pytest.approx(30)
    assert result["altitude_basis"] == "geometric"


def test_altitude_basis_selects_a_common_geometric_source():
    a = {"geo_altitude_m": 100, "baro_altitude_m": 200}
    b = {"geo_altitude_m": 110, "baro_altitude_m": 500}
    assert select_altitude_basis(a, b, "common_geometric_then_barometric") == ("geometric", 100, 110)


def test_altitude_basis_falls_back_to_common_barometric_source():
    a = {"geo_altitude_m": None, "baro_altitude_m": 200}
    b = {"geo_altitude_m": 110, "baro_altitude_m": 210}
    assert select_altitude_basis(a, b, "common_geometric_then_barometric") == ("barometric", 200, 210)


def test_mixed_altitude_sources_are_unavailable_instead_of_mixed():
    a = {"geo_altitude_m": 100, "baro_altitude_m": None}
    b = {"geo_altitude_m": None, "baro_altitude_m": 110}
    assert select_altitude_basis(a, b, "common_geometric_then_barometric") == ("unavailable", None, None)

    result = cpa((0, 0, 10, 0), (200, 0, -10, 0), altitude_basis="unavailable")
    assert result["v_sep_cpa_m"] is None
    assert result["current_v_sep_m"] is None
    assert result["vertical_separation_reason"] == "NO_COMMON_ALTITUDE_BASIS"
