from backend.airwatch.event_store import SQLiteEventStore


def event(event_id, *, mode="LIVE", status="ACTIVE", pair="a|b", risk="HIGH", timestamp=1000):
    return {"event_id": event_id, "event_type": "AIR_CONFLICT", "status": status, "mode": mode,
            "risk_profile": "research_default", "airport": "VOMM",
            "alert_kind": "ALERT",
            "aircraft_1": {"icao24": "a", "callsign": "ALPHA"},
            "aircraft_2": {"icao24": "b", "callsign": "BRAVO"}, "pair_key": pair,
            "first_seen_ts": timestamp, "last_updated_ts": timestamp + 10, "opened_ts": timestamp + 5, "resolved_ts": None,
            "resolution_reason": None, "current_risk": risk, "peak_risk": risk,
            "h_sep_cpa_m": 700, "min_h_sep_cpa_m": 650, "v_sep_cpa_m": 200,
            "min_v_sep_cpa_m": 180, "time_to_cpa_s": 35, "min_time_to_cpa_s": 30,
            "closing_speed_mps": 12, "altitude_basis": "geometric", "data_age_a_s": 5,
            "data_age_b_s": 7, "reasons": ["Research threshold met."],
            "score_components": {"adjusted_horizontal_m": 650}, "confidence": "HIGH",
            "aircraft_states_first": {"a": {"x_m": -100}}, "aircraft_states_peak": {"b": {"x_m": 100}},
            "cpa_lat": 12.95, "cpa_lon": 80.15, "cpa_aircraft_a_lat": 12.94,
            "cpa_aircraft_a_lon": 80.14, "cpa_aircraft_b_lat": 12.96, "cpa_aircraft_b_lon": 80.16,
            "cycles_observed": 2, "confirmation_elapsed_s": 30, "confirmation_started_ts": timestamp,
            "confirmation_cycles": 2, "continuation_count": 0, "duration_s": 10, "last_seen_ts": timestamp + 10,
            "below_threshold_since_ts": None, "below_threshold_cycles": 0, "missing_since_ts": None}


def test_insert_update_and_filter_events(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.db")
    store.initialize()
    import asyncio
    asyncio.run(store.upsert_many([event("live-1"), event("replay-1", mode="REPLAY"),
                                  event("resolved-1", status="RESOLVED", risk="MEDIUM", timestamp=900)]))
    updated = event("live-1", risk="CRITICAL")
    updated["h_sep_cpa_m"] = 300
    asyncio.run(store.upsert_many([updated]))

    assert store.get("live-1")["h_sep_cpa_m"] == 300
    assert store.get("live-1")["score_components"] == {"adjusted_horizontal_m": 650}
    assert len(store.active(mode="LIVE")) == 1
    assert store.active(mode="REPLAY")[0]["mode"] == "REPLAY"
    assert [item["event_id"] for item in store.query(mode="LIVE", status="RESOLVED")] == ["resolved-1"]
    assert [item["event_id"] for item in store.query(mode="LIVE", risk="CRITICAL")] == ["live-1"]
    assert [item["event_id"] for item in store.query(mode="LIVE", status="ACTIVE", aircraft="B")] == ["live-1"]
    assert [item["event_id"] for item in store.query(mode="LIVE", start_ts=950, end_ts=1100)] == ["live-1"]
