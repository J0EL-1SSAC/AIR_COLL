from copy import deepcopy

import pytest

from backend.airwatch.risk import evaluate_pair_risk, validate_timing_settings


@pytest.fixture
def settings():
    levels = {
        "LOW": {"max_horizontal_separation_m": 1000, "max_vertical_separation_m": 1000, "max_time_to_cpa_s": 60},
        "MEDIUM": {"max_horizontal_separation_m": 800, "max_vertical_separation_m": 800, "max_time_to_cpa_s": 50},
        "HIGH": {"max_horizontal_separation_m": 600, "max_vertical_separation_m": 600, "max_time_to_cpa_s": 40},
        "CRITICAL": {"max_horizontal_separation_m": 400, "max_vertical_separation_m": 400, "max_time_to_cpa_s": 30},
    }
    profile = {"levels": levels, "uncertainty_factor": 0.0,
               "adjustments": {"closing_speed_escalation_mps": 999, "closing_speed_raise_levels": 1,
                               "poor_data_age_s": 999, "downgrade_poor_data": False},
               "in_trail": {"enabled": False, "max_track_difference_deg": 20,
                            "min_lateral_offset_m": 1000, "downgrade_levels": 1}}
    return {"active_profile": "research_default", "profiles": {"research_default": profile},
            "lookahead_s": 60, "max_data_age_for_alert_s": 75,
            "treat_unknown_vertical_as": "conservative",
            "filters": {"ignore_both_on_ground": True, "ignore_diverging": True,
                        "ignore_beyond_lookahead": True, "ignore_old_data": True},
            "confidence": {"high_max_age_s": 15, "high_max_uncertainty_m": 250,
                           "medium_max_age_s": 45, "medium_max_uncertainty_m": 750}}


def pair(**updates):
    result = {"pair_key": "a|b", "aircraft_a": {"icao24": "a", "callsign": "A"},
              "aircraft_b": {"icao24": "b", "callsign": "B"}, "h_sep_cpa_m": 950,
              "v_sep_cpa_m": 950, "t_cpa_s": 55, "t_cpa_raw_s": 55,
              "closing_speed_mps": 5, "converging": True, "cpa_in_past": False,
              "data_age_a_s": 3, "data_age_b_s": 4, "combined_uncertainty_m": 0,
              "altitude_basis": "geometric"}
    result.update(updates)
    return result


@pytest.mark.parametrize(("metrics", "expected"), [
    ({}, "LOW"),
    ({"h_sep_cpa_m": 750, "v_sep_cpa_m": 750, "t_cpa_s": 45}, "MEDIUM"),
    ({"h_sep_cpa_m": 550, "v_sep_cpa_m": 550, "t_cpa_s": 35}, "HIGH"),
    ({"h_sep_cpa_m": 350, "v_sep_cpa_m": 350, "t_cpa_s": 25}, "CRITICAL"),
])
def test_each_level_boundary_requires_all_thresholds(settings, metrics, expected):
    assert evaluate_pair_risk(pair(**metrics), settings)["level"] == expected


def test_null_vertical_policies(settings):
    item = pair(v_sep_cpa_m=None)
    conservative = evaluate_pair_risk(item, settings)
    assert conservative["level"] == "LOW"
    assert any("unavailable" in reason.lower() for reason in conservative["reasons"])
    skipped = deepcopy(settings); skipped["treat_unknown_vertical_as"] = "skip"
    assert evaluate_pair_risk(item, skipped)["filtered_reason"] == "vertical_unavailable"
    downgraded = deepcopy(settings); downgraded["treat_unknown_vertical_as"] = "downgrade_one_level"
    assert evaluate_pair_risk(item, downgraded)["level"] == "NORMAL"


def test_uncertainty_adjustment_is_applied_and_explained(settings):
    profile = settings["profiles"]["research_default"]
    profile["uncertainty_factor"] = 1
    result = evaluate_pair_risk(pair(h_sep_cpa_m=1200, combined_uncertainty_m=300), settings)
    assert result["score_components"]["adjusted_horizontal_m"] == 900
    assert result["level"] == "LOW"
    assert any("modeled uncertainty" in reason for reason in result["reasons"])


def test_in_trail_downgrade_uses_track_and_lateral_offset(settings):
    settings["profiles"]["research_default"]["in_trail"]["enabled"] = True
    states = {"a": {"track_deg": 0, "analysis_x_m": 0, "analysis_y_m": 0},
              "b": {"track_deg": 0, "analysis_x_m": 1600, "analysis_y_m": 100}}
    result = evaluate_pair_risk(pair(h_sep_cpa_m=750, v_sep_cpa_m=750, t_cpa_s=45), settings,
                                states_by_id=states)
    assert result["level"] == "LOW"
    assert any("in-trail" in reason for reason in result["reasons"])


def test_poor_quality_flags_downgrade_one_level_and_explain(settings):
    settings["profiles"]["research_default"]["adjustments"].update(
        downgrade_poor_data=True, poor_data_age_s=60, poor_data_quality_flags=["LOW_QUALITY"])
    states = {"a": {"quality_flags": ["LOW_QUALITY"]}, "b": {"quality_flags": []}}
    result = evaluate_pair_risk(pair(h_sep_cpa_m=550, v_sep_cpa_m=550, t_cpa_s=35), settings,
                                states_by_id=states)
    assert result["level"] == "MEDIUM"
    assert any("data quality flags" in reason for reason in result["reasons"])


@pytest.mark.parametrize(("change", "reason"), [
    ({"converging": False}, "diverging"),
    ({"cpa_in_past": True}, "diverging"),
    ({"t_cpa_s": 61, "t_cpa_raw_s": 61}, "beyond_lookahead"),
    ({"data_age_b_s": 76}, "data_too_old"),
])
def test_context_filters_can_be_disabled(settings, change, reason):
    result = evaluate_pair_risk(pair(**change), settings)
    assert result["filtered_reason"] == reason
    switch = {"diverging": "ignore_diverging", "beyond_lookahead": "ignore_beyond_lookahead",
              "data_too_old": "ignore_old_data"}[reason]
    settings["filters"][switch] = False
    assert evaluate_pair_risk(pair(**change), settings)["filtered_reason"] is None


def test_ground_context_filter_can_be_disabled(settings):
    states = {"a": {"on_ground": True}, "b": {"on_ground": True}}
    assert evaluate_pair_risk(pair(), settings, states_by_id=states)["filtered_reason"] == "both_on_ground"
    settings["filters"]["ignore_both_on_ground"] = False
    assert evaluate_pair_risk(pair(), settings, states_by_id=states)["filtered_reason"] is None


def test_timing_validation_warns_when_age_limit_is_not_over_poll():
    config = {"collector": {"poll_interval_s": 30}, "risk": {
        "max_data_age_for_alert_s": 30, "active_profile": "p",
        "confidence": {"high_max_age_s": 15, "medium_max_age_s": 45},
        "profiles": {"p": {"adjustments": {"poor_data_age_s": 45}}}},
        "prediction": {"max_data_age_s": 45}, "state_manager": {"low_quality_after_s": 45}}
    assert "risk.max_data_age_for_alert_s" in validate_timing_settings(config)[0]
