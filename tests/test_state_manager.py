from backend.airwatch.models import AircraftState
from backend.airwatch.state_manager import AircraftStateManager


CONFIG = dict(latitude=12.9941, longitude=80.1709, history_length=2,
              low_quality_after_s=45, drop_after_s=180, tombstone_s=300,
              low_altitude_ft=1000, smoothing_enabled=False, alpha=0.75, beta=0.08)


def observation(*, latitude=12.9941, longitude=80.1709, timestamp=1000.0,
                altitude=1000.0, speed=100.0, track=90.0, on_ground=False):
    return AircraftState("testicao", "TEST", latitude, longitude, altitude, altitude,
                         speed, track, 0.0, on_ground, timestamp, timestamp)


def test_manager_projects_to_airport_enu_and_computes_velocity_components():
    manager = AircraftStateManager(**CONFIG)
    manager.update([observation()], 1001.0)
    result = manager.snapshot(1001.0)[0]
    assert abs(result["x_m"]) < 0.01
    assert abs(result["y_m"]) < 0.01
    assert result["vx_mps"] > 99
    assert abs(result["vy_mps"]) < 0.01
    assert result["analysis_eligible"] is True


def test_duplicate_position_timestamp_does_not_extend_trail():
    manager = AircraftStateManager(**CONFIG)
    manager.update([observation()], 1001.0)
    manager.update([observation(longitude=80.18)], 1002.0)
    result = manager.snapshot(1002.0)[0]
    assert len(result["history"]) == 1
    assert result["history"][0]["longitude"] == 80.1709


def test_age_compensation_is_separate_from_observed_enu_position():
    manager = AircraftStateManager(**CONFIG)
    manager.update([observation()], 1001.0)
    result = manager.snapshot(1001.0)[0]
    assert abs(result["x_m"]) < 0.01
    assert result["analysis_x_m"] > 99
    assert result["analysis_x_m"] != result["x_m"]


def test_old_position_is_low_quality_and_not_analysis_eligible():
    manager = AircraftStateManager(**CONFIG)
    manager.update([observation(timestamp=900.0)], 1001.0)
    result = manager.snapshot(1001.0)[0]
    assert "LOW_QUALITY" in result["quality_flags"]
    assert "stale_position" in result["quality_flags"]
    assert result["analysis_eligible"] is False


def test_missing_fields_are_flagged_and_analysis_is_excluded():
    manager = AircraftStateManager(**CONFIG)
    manager.update([observation(latitude=None, longitude=None, altitude=None, speed=None, track=None, on_ground=None)], 1001.0)
    result = manager.states["testicao"]
    view = manager._serialize("testicao", result, 1001.0)
    assert {"position_missing", "altitude_missing", "velocity_missing", "on_ground_ambiguous"} <= set(view["quality_flags"])
    assert view["analysis_eligible"] is False
    assert manager.snapshot(1001.0) == []


def test_drop_retains_a_short_last_seen_record():
    manager = AircraftStateManager(**CONFIG)
    manager.update([observation()], 1001.0)
    manager.expire(1182.0)
    assert "testicao" not in manager.states
    assert "testicao" in manager.recently_lost
    manager.expire(1483.0)
    assert "testicao" not in manager.recently_lost
