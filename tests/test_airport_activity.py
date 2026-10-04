from types import SimpleNamespace

from backend.airwatch.airport_activity import infer_departure, infer_landing
from backend.airwatch.activity_store import AirportActivityStore


def rules():
    return {"landing_inference_max_distance_nm":5,"landing_inference_max_height_ft":1500,"min_samples":2,
        "medium_confidence_min_samples":3,"high_confidence_min_samples":5,"max_heading_diff_deg":15,
        "departure_min_samples":2,"departure_min_climb_fpm":500,"departure_max_distance_nm":1.5,
        "departure_max_height_ft":3000}


def test_landing_is_inferred_only_with_terminal_low_aligned_descending_track():
    approach={"icao24":"abc123","callsign":"TEST1","outcome":"lost_in_corridor","state":"LOST",
        "samples":3,"altitude_m":300,"distance_to_threshold_nm":2.9,"heading_difference_deg":5,
        "vertical_rate_fpm":-400,"last_seen_ts":12,"observed_latitude":12.9,"observed_longitude":80.1}
    result=infer_landing(approach,rules(),airport_elevation_m=0)
    assert result["inferred"] and result["activity_type"]=="LIKELY_LANDED"
    assert result["latitude"]==12.9 and result["runway_end"]=="ambiguous"
    assert infer_landing({**approach,"outcome":"go_around_suspected"},rules(),airport_elevation_m=0) is None
    assert infer_landing({**approach,"samples":1},rules(),airport_elevation_m=0) is None
    assert infer_landing({**approach,"distance_to_threshold_nm":6},rules(),airport_elevation_m=0) is None


def test_departure_inference_needs_climb_alignment_and_near_end():
    end=SimpleNamespace(identifier="07",x_m=0,y_m=0,true_heading_deg=90)
    state={"icao24":"abc123","callsign":"TEST2","x_m":100,"y_m":0,"track_deg":90,
        "vertical_rate_mps":4,"geo_altitude_m":300,"baro_altitude_m":None,"first_seen":100,
        "history":[{"x_m":20,"y_m":0,"latitude":12.0,"longitude":80.0},{"x_m":100,"y_m":0,"latitude":12.0,"longitude":80.001}]}
    result=infer_departure(state,[end],rules(),airport_elevation_m=0)
    assert result and result["inferred"] and result["runway_end"]=="07"
    assert infer_departure({**state,"track_deg":180},[end],rules(),airport_elevation_m=0) is None
    assert infer_departure({**state,"vertical_rate_mps":0},[end],rules(),airport_elevation_m=0) is None


def test_activity_store_is_mode_tagged_and_deduplicates(tmp_path):
    store=AirportActivityStore(tmp_path/"activity.sqlite");store.initialize()
    item={"activity_type":"LIKELY_LANDED","icao24":"abc123","runway_end":"07","time_ts":100,
          "inferred":True,"confidence":"LOW","latitude":12,"longitude":80,"evidence":[]}
    store.upsert("LIVE",item);store.upsert("LIVE",item)
    assert len(store.query("LIVE"))==1 and store.query("REPLAY")==[]
