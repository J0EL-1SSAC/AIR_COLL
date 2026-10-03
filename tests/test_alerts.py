from copy import deepcopy

import pytest

from backend.airwatch.alerts import AlertManager


@pytest.fixture
def settings():
    thresholds = {
        "LOW": {"max_horizontal_separation_m": 3000, "max_vertical_separation_m": 900, "max_time_to_cpa_s": 120},
        "MEDIUM": {"max_horizontal_separation_m": 1852, "max_vertical_separation_m": 600, "max_time_to_cpa_s": 90},
        "HIGH": {"max_horizontal_separation_m": 926, "max_vertical_separation_m": 300, "max_time_to_cpa_s": 60},
        "CRITICAL": {"max_horizontal_separation_m": 463, "max_vertical_separation_m": 150, "max_time_to_cpa_s": 30},
    }
    profile = {"levels": thresholds, "uncertainty_factor": 0,
               "adjustments": {"closing_speed_escalation_mps": 999, "closing_speed_raise_levels": 1,
                               "poor_data_age_s": 999, "downgrade_poor_data": False},
               "in_trail": {"enabled": False, "max_track_difference_deg": 20,
                            "min_lateral_offset_m": 1000, "downgrade_levels": 1}}
    return {"active_profile": "research_default", "profiles": {"research_default": profile},
            "min_alert_level": "MEDIUM", "allow_low_watch": False,
            "lookahead_s": 60, "max_data_age_for_alert_s": 75, "treat_unknown_vertical_as": "conservative",
            "filters": {"ignore_both_on_ground": True, "ignore_diverging": True,
                        "ignore_beyond_lookahead": True, "ignore_old_data": True},
            "confirmation": {"confirm_cycles": 2, "confirm_min_seconds": 30},
            "clear": {"clear_below_level": "MEDIUM", "clear_cycles": 2, "clear_min_seconds": 30},
            "cooldown_s": 300, "data_lost_timeout_s": 90,
            "confidence": {"high_max_age_s": 15, "high_max_uncertainty_m": 250,
                           "medium_max_age_s": 45, "medium_max_uncertainty_m": 750}}


def make_pair(reverse=False, *, h=1000, v=400, t=55):
    a, b = {"icao24": "a", "callsign": "ALPHA"}, {"icao24": "b", "callsign": "BRAVO"}
    if reverse:
        a, b = b, a
    return {"pair_key": f"{a['icao24']}|{b['icao24']}", "aircraft_a": a, "aircraft_b": b,
            "h_sep_cpa_m": h, "v_sep_cpa_m": v, "t_cpa_s": t, "t_cpa_raw_s": t,
            "closing_speed_mps": 10, "converging": True, "cpa_in_past": False,
            "data_age_a_s": 2, "data_age_b_s": 3, "combined_uncertainty_m": 0,
            "altitude_basis": "geometric",
            "cpa_position": {"aircraft_a": {"latitude": 12.9, "longitude": 80.1},
                              "aircraft_b": {"latitude": 13.0, "longitude": 80.2},
                              "midpoint": {"latitude": 12.95, "longitude": 80.15}}}


def states():
    return [{"icao24": "a", "callsign": "ALPHA", "on_ground": False, "latitude": 12.9,
             "longitude": 80.1, "analysis_x_m": -100, "analysis_y_m": 0, "x_m": -100, "y_m": 0,
             "vx_mps": 10, "vy_mps": 0, "track_deg": 90, "age_s": 2, "quality_flags": []},
            {"icao24": "b", "callsign": "BRAVO", "on_ground": False, "latitude": 13.0,
             "longitude": 80.2, "analysis_x_m": 100, "analysis_y_m": 0, "x_m": 100, "y_m": 0,
             "vx_mps": -10, "vy_mps": 0, "track_deg": 270, "age_s": 3, "quality_flags": []}]


def manager(settings, mode="LIVE", restored=()):
    return AlertManager(settings=settings, airport="VOMM", mode=mode,
                        risk_profile=settings["active_profile"], restored_events=restored)


def test_confirmation_requires_both_cycles_and_pipeline_seconds(settings):
    alerts = manager(settings)
    first = alerts.process_cycle([make_pair()], states(), 1000)
    assert first[0]["status"] == "CANDIDATE"
    assert not any(isinstance(item, dict) and item.get("sub_event") == "opened" for item in first)
    before_time = alerts.process_cycle([make_pair()], states(), 1029)
    assert before_time[0]["status"] == "CANDIDATE"
    opened = alerts.process_cycle([make_pair()], states(), 1030)
    assert opened[0]["sub_event"] == "opened"
    event = opened[0]["event"]
    assert event["status"] == "ACTIVE"
    assert event["confirmation_elapsed_s"] == 30
    assert event["confirmation_cycles"] == 3


def test_hysteresis_resolves_after_both_clear_cycles_and_elapsed_time(settings):
    alerts = manager(settings)
    alerts.process_cycle([make_pair()], states(), 1000)
    active = alerts.process_cycle([make_pair()], states(), 1030)[0]["event"]
    low_pair = make_pair(h=5000, v=1000, t=55)
    assert alerts.process_cycle([low_pair], states(), 1060)[0]["event"]["status"] == "ACTIVE"
    resolved = alerts.process_cycle([low_pair], states(), 1090)[0]
    assert resolved["sub_event"] == "resolved"
    assert resolved["event"]["resolution_reason"] == "threshold_cleared"
    assert resolved["event"]["event_id"] == active["event_id"]


def test_escalation_updates_the_same_record(settings):
    alerts = manager(settings)
    alerts.process_cycle([make_pair()], states(), 1000)
    event_id = alerts.process_cycle([make_pair()], states(), 1030)[0]["event"]["event_id"]
    escalation = alerts.process_cycle([make_pair(h=500, v=100, t=20)], states(), 1060)[0]
    assert escalation["sub_event"] == "escalated"
    assert escalation["event"]["event_id"] == event_id
    assert escalation["event"]["peak_risk"] == "HIGH"
    assert escalation["event"]["min_h_sep_cpa_m"] == 500


def test_missing_aircraft_resolves_as_data_lost(settings):
    alerts = manager(settings)
    alerts.process_cycle([make_pair()], states(), 1000)
    alerts.process_cycle([make_pair()], states(), 1030)
    alerts.process_cycle([], states()[:1], 1040)
    result = alerts.process_cycle([], states()[:1], 1130)
    assert result[0]["event"]["resolution_reason"] == "data_lost"
    assert result[0]["sub_event"] == "resolved"


def test_cooldown_reuses_same_event_id_and_records_continuation(settings):
    alerts = manager(settings)
    alerts.process_cycle([make_pair()], states(), 1000)
    original = alerts.process_cycle([make_pair()], states(), 1030)[0]["event"]
    weak = make_pair(h=5000, v=1000)
    alerts.process_cycle([weak], states(), 1060)
    alerts.process_cycle([weak], states(), 1090)
    continued = alerts.process_cycle([make_pair()], states(), 1200)[0]
    assert continued["event_id"] == original["event_id"]
    assert continued["continuation_count"] == 1
    assert any("Continuation" in reason for reason in continued["reasons"])


def test_events_are_canonical_and_replay_mode_is_separate(settings):
    live = manager(settings)
    reverse = live.process_cycle([make_pair(reverse=True)], states(), 1000)[0]
    normal = live.process_cycle([make_pair()], states(), 1001)[0]
    assert reverse["pair_key"] == normal["pair_key"] == "a|b"
    replay = manager(settings, mode="REPLAY")
    replay.process_cycle([make_pair()], states(), 1000)
    replay_event = replay.process_cycle([make_pair()], states(), 1030)[0]["event"]
    live_event = live.process_cycle([make_pair()], states(), 1030)[0]["event"]
    assert replay_event["mode"] == "REPLAY"
    assert replay_event["event_id"] != live_event["event_id"]


def test_finalize_closes_at_replay_boundary_without_claiming_clear(settings):
    alerts = manager(settings, mode="REPLAY")
    alerts.process_cycle([make_pair()], states(), 1000)
    alerts.process_cycle([make_pair()], states(), 1030)
    ended = alerts.finalize(1030, "replay_ended")
    assert ended[0]["event"]["resolution_reason"] == "replay_ended"


def test_low_level_is_ignored_or_labeled_as_watch_by_configuration(settings):
    alerts = manager(settings)
    assert alerts.process_cycle([make_pair(h=2500, v=800, t=55)], states(), 1000) == []
    settings["allow_low_watch"] = True
    alerts = manager(settings)
    candidate = alerts.process_cycle([make_pair(h=2500, v=800, t=55)], states(), 1000)[0]
    assert candidate["alert_kind"] == "WATCH"
