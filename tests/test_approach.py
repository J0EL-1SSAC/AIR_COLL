import math
from types import SimpleNamespace

from shapely.geometry import Polygon

from backend.airwatch.approach import ApproachTracker, evaluate_approach


def settings(**overrides):
    values = {"closing_window_s": 60, "min_closing_samples": 2, "min_closing_distance_m": 50,
              "max_heading_diff_deg": 15, "max_vertical_rate_fpm": 500, "min_glide_angle_deg": 1,
              "max_glide_angle_deg": 6, "min_speed_kt": 60, "max_speed_kt": 220,
              "max_data_age_s": 75, "speed_uncertainty_mps": 8, "age_distance_uncertainty_mps": 8,
              "confidence_min_samples_medium": 3, "confidence_min_samples_high": 5,
              "confirm_s": 20, "near_threshold_distance_m": 1500, "clear_hysteresis_s": 30, "lost_timeout_s": 90,
              "go_around_climb_fpm": 500}
    values.update(overrides)
    return values


def runway_end():
    # Test-only synthetic ENU geometry. It is not imported by application runtime.
    return SimpleNamespace(identifier="09", x_m=0.0, y_m=0.0, true_heading_deg=90.0,
        elevation_m=0.0, corridor_polygon=Polygon([(-4000,-900),(100,-900),(100,900),(-4000,900)]), altitude_ceiling_m=1500)


def state(x, t, **kw):
    item = {"icao24":"test01", "callsign":"TEST", "analysis_x_m":x, "analysis_y_m":0,
        "x_m":x, "y_m":0, "track_deg":90, "velocity_mps":70, "vertical_rate_mps":-2,
        "geo_altitude_m":300, "baro_altitude_m":400, "on_ground":False, "age_s":3,
        "quality_flags":[], "history":[]}
    item.update(kw)
    item["history"] = [{"timestamp":t-30,"x_m":x-300,"y_m":0},{"timestamp":t,"x_m":x,"y_m":0}]
    return item


def test_aligned_descending_candidate_and_eta_window():
    result = evaluate_approach(state(-3000,100), runway_end(), settings(), 100)
    assert result["eligible"]
    assert result["distance_to_threshold_m"] == 3000
    assert result["cross_track_error_m"] == pytest.approx(0, abs=1e-9)
    assert result["eta_window_s"]["min"] < result["eta_window_s"]["max"]
    assert result["implied_glide_angle_deg"] == pytest.approx(math.degrees(math.atan2(300,3000)))


def test_rejects_high_off_heading_stale_and_moving_away():
    end = runway_end(); cfg = settings()
    assert not evaluate_approach(state(-3000,100,geo_altitude_m=3000,baro_altitude_m=3000),end,cfg,100)["eligible"]
    assert not evaluate_approach(state(-3000,100,track_deg=120),end,cfg,100)["eligible"]
    assert not evaluate_approach(state(-3000,100,age_s=100),end,cfg,100)["eligible"]
    moving_away = state(-3000,100)
    moving_away["history"] = [{"timestamp":70,"x_m":-2800,"y_m":0},{"timestamp":100,"x_m":-3000,"y_m":0}]
    assert not evaluate_approach(moving_away,end,cfg,100)["eligible"]


def test_wraparound_and_level_glidepath_logic():
    # runway heading 10, track 350 is a 20-degree wrap difference
    end = SimpleNamespace(**{**runway_end().__dict__, "true_heading_deg":10.0})
    item = state(-3000,100,track_deg=350,vertical_rate_mps=0,geo_altitude_m=180)
    item["history"] = [{"timestamp":70,"x_m":-3300,"y_m":0},{"timestamp":100,"x_m":-3000,"y_m":0}]
    assert evaluate_approach(item,end,settings(max_heading_diff_deg=20),100)["checks"]["heading_aligned"]
    assert evaluate_approach(item,end,settings(max_heading_diff_deg=20),100)["checks"]["descent_or_glidepath"]


def test_confirmation_clock_and_replay_determinism():
    cfg=settings()
    tracker=ApproachTracker(cfg); end=runway_end()
    a=state(-3000,100)
    first=tracker.update([a],[SimpleNamespace(end_a=end,end_b=SimpleNamespace(**{**end.__dict__,"identifier":"27","true_heading_deg":270}))],100)
    assert first and first[0]["state"]=="CANDIDATE"
    a["history"].append({"timestamp":100,"x_m":-3000,"y_m":0})
    result=tracker.update([a],[SimpleNamespace(end_a=end,end_b=SimpleNamespace(**{**end.__dict__,"identifier":"27","true_heading_deg":270}))],130)
    assert result and result[0]["state"] in {"LIKELY_APPROACHING","NEAR_THRESHOLD"}
    tracker2=ApproachTracker(cfg)
    tracker2.update([a],[SimpleNamespace(end_a=end,end_b=SimpleNamespace(**{**end.__dict__,"identifier":"27","true_heading_deg":270}))],100)
    assert tracker2.update([a],[SimpleNamespace(end_a=end,end_b=SimpleNamespace(**{**end.__dict__,"identifier":"27","true_heading_deg":270}))],130)[0]["state"]==result[0]["state"]


def test_go_around_suspected_after_climb_and_receding_position():
    end=runway_end(); runway=SimpleNamespace(end_a=end,end_b=SimpleNamespace(**{**end.__dict__,"identifier":"27","true_heading_deg":270}))
    tracker=ApproachTracker(settings())
    tracker.update([state(-3000,100)],[runway],100)
    receding=state(-4100,130,vertical_rate_mps=5)
    result=tracker.update([receding],[runway],130)
    assert any(item["state"]=="GO_AROUND_SUSPECTED" for item in result)


def test_tracker_counts_distinct_position_timestamps_not_repeated_polls():
    end=runway_end(); runway=SimpleNamespace(end_a=end,end_b=SimpleNamespace(**{**end.__dict__,"identifier":"27","true_heading_deg":270}))
    tracker=ApproachTracker(settings())
    first=state(-3000,100,position_timestamp=95)
    second=state(-3000,130,position_timestamp=95)
    assert tracker.update([first],[runway],100)[0]["samples"]==1
    assert tracker.update([second],[runway],130)[0]["samples"]==1
    third=state(-2800,160,position_timestamp=155)
    assert tracker.update([third],[runway],160)[0]["samples"]==2


def test_terminal_approach_starts_a_new_segment_after_configured_gap():
    end=runway_end(); runway=SimpleNamespace(end_a=end,end_b=SimpleNamespace(**{**end.__dict__,"identifier":"27","true_heading_deg":270}))
    tracker=ApproachTracker(settings(restart_after_s=300))
    first=state(-3000,100,position_timestamp=95)
    tracker.update([first],[runway],100)
    tracker.update([],[runway],200)
    assert tracker.tracks[("test01","09")]["state"]=="LOST"
    reappeared=state(-2500,500,position_timestamp=495)
    result=tracker.update([reappeared],[runway],500)
    end_result=next(item for item in result if item["runway_end"]=="09")
    assert end_result["first_seen_ts"]==500
    assert end_result["samples"]==1


import pytest
