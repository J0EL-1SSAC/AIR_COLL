from backend.airwatch.frequencies import estimate_aircraft_facilities


def test_radio_aircraft_entries_are_keyed_by_icao24_not_position():
    states=[{"icao24":"abc001","callsign":"ONE","on_ground":True,"age_s":1},
            {"icao24":"abc002","callsign":"TWO","on_ground":True,"age_s":1}]
    result=estimate_aircraft_facilities(states,[],{"facilities":{"GND":[{"type":"GND","frequency_mhz":121.9}]}},
        {"short_final_distance_nm":3,"departure_climb_min_fpm":300,"departure_distance_nm":10,
         "departure_max_altitude_ft":10000,"departure_fallback_type":"APP"})
    assert set(result["estimates"])=={"abc001","abc002"}
    assert result["estimates"]["abc002"]["state"]["callsign"]=="TWO"
