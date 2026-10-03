import sqlite3

import pytest

from backend.airwatch.coverage_analysis import analyze_coverage, render_markdown


def _database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE raw_states (
          fetch_time REAL, icao24 TEXT, callsign TEXT, latitude REAL, longitude REAL,
          baro_altitude_m REAL, geo_altitude_m REAL, velocity_mps REAL, track_deg REAL,
          vertical_rate_mps REAL, on_ground INTEGER, position_timestamp REAL, last_contact REAL
        );
        CREATE TABLE coverage_samples (
          sample_time REAL, aircraft_count INTEGER, below_threshold_count INTEGER,
          on_ground_count INTEGER, mean_data_age_s REAL
        );
        """)
        for timestamp in (100, 130, 160):
            connection.execute("INSERT INTO coverage_samples VALUES (?,1,1,0,2)", (timestamp,))
            connection.execute("INSERT INTO raw_states VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (timestamp, "testicao", "TEST", 0.0, 0.0, 100.0, 120.0,
                                50.0, 0.0, -1.0, 0, timestamp - 2, timestamp))


def _settings(minimum_hours):
    return {"max_poll_gap_s": 90, "max_track_gap_s": 180, "minimum_track_reports": 3,
            "altitude_bands_ft": [[0, 500, "0-500 ft"], [500, 1000, "500-1,000 ft"],
                                  [1000, 2000, "1,000-2,000 ft"], [2000, 3000, "2,000-3,000 ft"],
                                  [3000, 5000, "3,000-5,000 ft"], [5000, None, "above 5,000 ft"]],
            "distance_rings_nm": [[0, 2, "0-2 NM"], [2, 5, "2-5 NM"], [5, 10, "5-10 NM"],
                                  [10, 20, "10-20 NM"], [20, 40, "20-40 NM"]],
            "approach_altitude_ceiling_m": 457.2,
            "feasibility": {"minimum_recording_hours": minimum_hours, "minimum_low_altitude_reports_per_hour": 0,
                            "minimum_ground_reports_per_hour": 0, "minimum_runway_buffer_reports_per_hour": 0}}


def test_coverage_report_uses_read_only_recorded_rows_and_bands(tmp_path):
    db = tmp_path / "recorded.sqlite"
    _database(db)
    settings = _settings(0)
    report = analyze_coverage(db_path=db, airport_latitude=0, airport_longitude=0,
                              airport_elevation_m=0, radius_nm=40, settings=settings, runways=None)
    assert report["recording"]["poll_cycles"] == 3
    assert report["recording"]["reports"] == 3
    assert report["altitude_bands"][1]["reports"] == 3
    assert report["update_intervals_s"]["median"] == pytest.approx(30)
    assert report["missing_shares"]["position"]["share"] == 0
    assert report["runways_available"] is False
    assert report["fadeout"]["ambiguous"] == 1
    assert "receivers did not see" in render_markdown(report)
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_states").fetchone()[0] == 3


def test_short_recording_reports_insufficient_data(tmp_path):
    db = tmp_path / "recorded.sqlite"
    _database(db)
    settings = _settings(1)
    report = analyze_coverage(db_path=db, airport_latitude=0, airport_longitude=0,
                              airport_elevation_m=0, radius_nm=40, settings=settings, runways=None)
    assert set(report["recommendations"].values()) == {"insufficient data"}
