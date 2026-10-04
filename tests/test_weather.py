from backend.airwatch.weather import select_metar, select_taf, utc_iso


def test_select_metar_uses_latest_station_report():
    records = [{"rawOb": "older", "obsTime": 100}, {"rawOb": "newer", "obsTime": 200}]
    assert select_metar(records)["rawOb"] == "newer"


def test_selectors_return_no_data_for_empty_or_unusable_provider_response():
    assert select_metar([]) is None
    assert select_metar([{"obsTime": 200}]) is None
    assert select_taf([]) is None
    assert select_taf([{"issueTime": 200}]) is None


def test_select_taf_uses_latest_issue_time():
    records = [{"rawTAF": "older", "issueTime": 100}, {"rawTAF": "newer", "issueTime": 200}]
    assert select_taf(records)["rawTAF"] == "newer"


def test_utc_iso_does_not_invent_a_timestamp():
    assert utc_iso(None) is None
    assert utc_iso(0) == "1970-01-01T00:00:00Z"
