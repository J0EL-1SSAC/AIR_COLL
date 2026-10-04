import logging

from backend.airwatch.frequencies import estimate_aircraft_facilities, estimate_controlling_facility, estimate_facility_roles, load_frequencies


def test_vomm_frequency_override_and_departure_fallback(tmp_path, caplog):
    source = tmp_path / "frequencies.csv"
    source.write_text("airport_ident,type,description,frequency_mhz\n"
                      "VOMM,APP,APP,127.9\nVOMM,GCA,SCHENNAI RADARS,125.7\n"
                      "VOMM,DEP,ignore,110.0\n", encoding="utf-8")
    override = tmp_path / "override.yaml"
    override.write_text("overrides:\n  - airport_ident: VOMM\n    type: GCA\n    frequency_mhz: 125.7\n    description: SCHENNAI RADARS\n    values:\n      description: CHENNAI RADARS\n    reason: spelling correction\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        rows = load_frequencies(airport_ident="VOMM", data_path=source, override_path=override)
    assert len(rows) == 2  # allowed selected types omit DEP
    assert next(row for row in rows if row["type"] == "GCA")["description"] == "CHENNAI RADARS"
    assert "Applying frequency override" in caplog.text
    estimate = estimate_facility_roles(rows)
    assert estimate["departure_estimate"]["source_type"] == "APP"
    assert estimate["departure_estimate"]["is_fallback"] is True
    assert "Verify against the official AIP" in estimate["departure_estimate"]["note"]
    assert estimate["clearance_estimate"]["source_type"] == "APP"


def test_facility_estimates_ground_final_approach_departure_fallback_and_acc():
    frequencies={"GND":[{"frequency_mhz":121.9}],"TWR":[{"frequency_mhz":118.1}],
                 "APP":[{"frequency_mhz":127.9}],"ACC":[{"frequency_mhz":118.9}]}
    rules={"short_final_distance_nm":1.5,"departure_climb_min_fpm":500,
           "departure_distance_nm":5,"departure_max_altitude_ft":3000,"departure_fallback_type":"APP"}
    def state(icao, **kw): return {"icao24":icao,"on_ground":False,"distance_nm":10,"altitude_ft":5000,"vertical_rate_mps":0,**kw}
    approach=[{"icao24":"a","distance_to_threshold_nm":0.8}]
    assert estimate_controlling_facility(state("g",on_ground=True),[],frequencies,rules)["facility_type"]=="GND"
    assert estimate_controlling_facility(state("a"),approach,frequencies,rules)["facility_type"]=="TWR"
    assert estimate_controlling_facility(state("b"),[{"icao24":"b","distance_to_threshold_nm":4}],frequencies,rules)["facility_type"]=="APP"
    dep=estimate_controlling_facility(state("c",distance_nm=3,altitude_ft=2000,vertical_rate_mps=4),[],frequencies,rules)
    assert dep["requested_type"]=="DEP" and dep["facility_type"]=="APP" and dep["fallback"]
    assert estimate_controlling_facility(state("d"),[],frequencies,rules)["facility_type"]=="ACC"
    result=estimate_aircraft_facilities([state("a")],approach,{"facilities":frequencies},rules)
    assert result["note"]=="Estimate. Not a radio observation."
