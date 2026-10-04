from backend.airwatch.weather import decode_metar, flight_category, runway_wind_components


def test_decodes_metar_and_computes_category():
    report = decode_metar({"icaoId":"VOMM", "rawOb":"VOMM 041230Z 28004KT 3000 BR FEW020 BKN080 28/25 Q1013",
        "obsTime":1791117000, "wdir":280, "wspd":4, "visib":2.0, "clouds":[{"cover":"BKN","base":8000}], "altim":1013})
    assert report["wind_direction_true_deg"] == 280
    assert report["qnh_hpa"] == 1013
    assert report["flight_category"] == "IFR"


def test_flight_category_uses_lowest_broken_ceiling_or_visibility():
    assert flight_category(0.75, []) == "LIFR"
    assert flight_category(2, [{"cover":"BKN","base":1200}]) == "IFR"
    assert flight_category(8, [{"cover":"BKN","base":2500}]) == "MVFR"
    assert flight_category(10, [{"cover":"FEW","base":100}]) == "VFR"


def test_runway_wind_components_include_gust_and_variable_direction():
    runway = [{"identifier":"09", "true_heading_deg":90}]
    component = runway_wind_components(90, 10, 20, runway)[0]
    assert component["headwind_kt"] == 10
    assert abs(component["crosswind_kt"]) < 1e-9
    assert component["gust_headwind_kt"] == 20
    assert abs(component["gust_crosswind_kt"]) < 1e-9
    unavailable = runway_wind_components(None, 10, None, runway)[0]
    assert unavailable["headwind_kt"] is None
    variable = decode_metar({"rawOb":"VOMM 041230Z 28004KT 250V310 9999 NSC", "wdir":280,"wspd":4,"visib":10})
    assert variable["wind_direction_variable"] is True
    assert variable["wind_direction_true_deg"] is None
