from types import SimpleNamespace

from shapely.geometry import box

from backend.airwatch.occupancy import OccupancyTracker


def test_gate_failure_is_unknown_and_never_clear():
    tracker=OccupancyTracker({"enabled":True})
    runway=SimpleNamespace(identifier="09/27",buffer_polygon=box(-100,-30,100,30))
    result=tracker.update([], [runway], 100, False, {"reason":"insufficient", "numbers":{"on_ground":0}})
    assert result["runways"]["09/27"]["status"]=="UNKNOWN"
    assert not result["runways"]["09/27"]["occupancy_assessable"]
    assert "not assessable" in result["runways"]["09/27"]["reason"]


def test_single_buffer_sample_is_only_near_runway():
    cfg={"enabled":True,"ground_elevation_m":0,"min_persistence_s":30,"min_consistent_samples":2,
         "max_data_age_s":75,"ghost_ttl_s":120,"ground_altitude_tolerance_m":15,"max_ground_speed_mps":25,
         "alignment_tolerance_deg":20,"crossing_min_angle_deg":55}
    tracker=OccupancyTracker(cfg)
    runway=SimpleNamespace(identifier="09/27",buffer_polygon=box(-100,-30,100,30),end_a=SimpleNamespace(true_heading_deg=90),end_b=SimpleNamespace(true_heading_deg=270))
    plane={"icao24":"abc123","x_m":0,"y_m":0,"age_s":2,"on_ground":True,"geo_altitude_m":1,"track_deg":90,"velocity_mps":5}
    status=tracker.update([plane],[runway],100,True,{})["runways"]["09/27"]
    assert status["status"]=="AIRCRAFT_NEAR_RUNWAY"
    assert status["aircraft"][0]["single_sample_or_extrapolation"]


def test_missing_report_keeps_lost_ghost_not_clear():
    cfg={"enabled":True,"ground_elevation_m":0,"min_persistence_s":30,"min_consistent_samples":2,
         "max_data_age_s":75,"ghost_ttl_s":120,"ground_altitude_tolerance_m":15,"max_ground_speed_mps":25,
         "alignment_tolerance_deg":20,"crossing_min_angle_deg":55}
    tracker=OccupancyTracker(cfg)
    runway=SimpleNamespace(identifier="09/27",buffer_polygon=box(-100,-30,100,30),end_a=SimpleNamespace(true_heading_deg=90),end_b=SimpleNamespace(true_heading_deg=270))
    plane={"icao24":"abc123","x_m":0,"y_m":0,"age_s":2,"on_ground":True,"geo_altitude_m":1,"track_deg":90,"velocity_mps":5}
    tracker.update([plane],[runway],100,True,{})
    result=tracker.update([],[runway],110,True,{})["runways"]["09/27"]
    assert result["status"]=="LOST_NEAR_RUNWAY"
    assert result["status"]!="NO_REPORTED_ACTIVITY"
