from backend.airwatch.airport_reference import AirportReferenceIndex


def test_reference_lookup_indexes_icao_and_iata_codes(tmp_path):
    source = tmp_path / "airports.csv"
    source.write_text("ident,icao_code,iata_code,name,latitude_deg,longitude_deg,elevation_ft,municipality\n"
                      "VOMM,VOMM,MAA,Chennai International Airport,12.99,80.17,52,Chennai\n", encoding="utf-8")
    index = AirportReferenceIndex(source, tmp_path / "reference.db")
    assert index.lookup("MAA")["name"] == "Chennai International Airport"
    assert index.lookup("VOMM")["latitude"] == 12.99
    assert index.lookup("ZZZZ") is None


def test_reference_index_rebuilds_when_source_file_changes(tmp_path):
    source = tmp_path / "airports.csv"
    source.write_text("ident,icao_code,iata_code,name,latitude_deg,longitude_deg,elevation_ft,municipality\n"
                      "VOMM,VOMM,MAA,Old Name,12.99,80.17,52,Chennai\n", encoding="utf-8")
    index = AirportReferenceIndex(source, tmp_path / "reference.db")
    assert index.lookup("VOMM")["name"] == "Old Name"
    source.write_text("ident,icao_code,iata_code,name,latitude_deg,longitude_deg,elevation_ft,municipality\n"
                      "VOMM,VOMM,MAA,Updated Name,12.99,80.17,52,Chennai\n", encoding="utf-8")
    assert index.lookup("VOMM")["name"] == "Updated Name"
