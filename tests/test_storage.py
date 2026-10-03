import sqlite3

from backend.airwatch.models import AircraftState
from backend.airwatch.storage import SQLiteRecorder


def test_raw_and_coverage_batches_are_recorded(tmp_path):
    path = tmp_path / "airwatch.db"
    recorder = SQLiteRecorder(path, batch_size=10, flush_interval_s=1, low_altitude_m=304.8,
                              low_quality_after_s=45)
    recorder._initialize()
    state = AircraftState("testicao", None, None, None, None, None, None, None,
                          None, None, 1000.0, 1001.0)
    recorder._write_batch([
        ("coverage", (1002.0, 1, 0, 0, 2.0)),
        ("state", (1002.0, "opensky", state, recorder.quality_flags(state, 1002.0, 45))),
    ])
    with sqlite3.connect(path) as connection:
        raw = connection.execute("SELECT latitude, baro_altitude_m, data_quality_flags FROM raw_states").fetchone()
        sample = connection.execute("SELECT aircraft_count FROM coverage_samples").fetchone()
    assert raw[0] is None and raw[1] is None
    assert "position_missing" in raw[2] and "altitude_missing" in raw[2]
    assert sample == (1,)


def test_coverage_buckets_use_integer_peak_counts(tmp_path):
    recorder = SQLiteRecorder(tmp_path / "coverage.db", batch_size=10, flush_interval_s=1,
                              low_altitude_m=304.8, low_quality_after_s=45)
    recorder._initialize()
    recorder._write_batch([
        ("coverage", (60.0, 4, 1, 0, 5.0)),
        ("coverage", (90.0, 6, 2, 1, 7.0)),
    ])
    result = recorder.coverage(since=0, bucket_s=60)["samples"]
    assert result == [{"bucket_start": 60, "aircraft_count": 6, "below_threshold_count": 2,
                       "on_ground_count": 1, "mean_data_age_s": 6.0}]
