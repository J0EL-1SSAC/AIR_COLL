from backend.airwatch.approach_store import SQLiteApproachStore


def test_approach_tracks_are_upserted_and_mode_separated(tmp_path):
    store=SQLiteApproachStore(tmp_path/"approaches.sqlite")
    store.initialize()
    row={"icao24":"abc123","callsign":"TEST","runway_end":"09","state":"CANDIDATE",
         "first_seen_ts":100.0,"last_seen_ts":130.0,"samples":2,"altitude_m":250.0,
         "distance_to_threshold_m":1800.0,"outcome":None,"eta_window_s":{"min":10,"max":20}}
    store.upsert("LIVE",row)
    updated={**row,"state":"LIKELY_APPROACHING","samples":3,"distance_to_threshold_m":1200.0}
    store.upsert("LIVE",updated)
    store.upsert("REPLAY",{**row,"state":"REPLAY_ONLY"})
    assert store.query("LIVE")==[updated]
    assert store.query("REPLAY")==[{**row,"state":"REPLAY_ONLY"}]
    assert store.query("LIVE",start=131)==[]
