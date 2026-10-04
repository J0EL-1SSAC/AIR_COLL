import logging

from backend.airwatch.frequencies import estimate_facility_roles, load_frequencies


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
