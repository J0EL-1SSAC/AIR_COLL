import asyncio
import sqlite3

from backend.airwatch.clock import ReplayClock
from backend.airwatch.models import AircraftState, AirportCenter
from backend.airwatch.replay import ReplaySource
from backend.airwatch.storage import SQLiteRecorder


def recorded_db(path):
    recorder = SQLiteRecorder(path, batch_size=10, flush_interval_s=1,
                              low_altitude_m=304.8, low_quality_after_s=45)
    recorder._initialize()
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO coverage_samples VALUES (1000,1,0,0,0)")
        connection.execute("INSERT INTO coverage_samples VALUES (1030,0,0,0,NULL)")
    return recorder


def test_replay_source_includes_empty_cycles_and_recorded_states_only(tmp_path):
    path = tmp_path / "recorded.db"
    recorder = recorded_db(path)
    state = AircraftState("abcd12", "TEST", 12.9941, 80.1709, 1000, 1100, 100, 90, 0, False,
                          999, 1000, '["captured"]')
    recorder._write_batch([("state", (1000.0, "opensky", state, []))])
    source = ReplaySource(path)
    center = AirportCenter("VOMM", 12.9941, 80.1709)
    assert source.total_cycles == 2
    first = asyncio.run(source.fetch_states(center, 40))
    assert first[0].icao24 == "abcd12"
    assert first[0].raw_payload_json == '["captured"]'
    assert source.current_cycle_time() == 1000
    assert asyncio.run(source.fetch_states(center, 40)) == []
    assert source.finished


def test_replay_source_filters_time_range(tmp_path):
    path = tmp_path / "range.db"
    recorded_db(path)
    source = ReplaySource(path, start_time=1010, end_time=1040)
    assert source.total_cycles == 1
    assert source.next_fetch_time == 1030


def test_replay_clock_tracks_source_time_and_speed():
    clock = ReplayClock()
    clock.set(1000)
    asyncio.run(clock.wait_until(1001, 1000000))
    assert clock.now() == 1001
    assert clock.isoformat().startswith("1970-01-01T00:16:41")
