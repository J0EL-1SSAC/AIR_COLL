import asyncio
import sqlite3
from pathlib import Path

import yaml
from pyproj import Transformer

from backend.airwatch.evaluation import evaluate_recorded_range, write_evaluation_report
from backend.airwatch.models import AircraftState
from backend.airwatch.storage import SQLiteRecorder


def recorded_database(path: Path):
    recorder = SQLiteRecorder(path, batch_size=20, flush_interval_s=1,
                              low_altitude_m=304.8, low_quality_after_s=45)
    recorder._initialize()
    config = yaml.safe_load(Path("config.yaml").read_text())
    airport = config["airport"]
    local = f"+proj=aeqd +lat_0={airport['latitude']} +lon_0={airport['longitude']} +datum=WGS84 +units=m +no_defs"
    inverse = Transformer.from_crs(local, "EPSG:4326", always_xy=True)
    for cycle, timestamp in enumerate((1000.0, 1030.0, 1060.0)):
        elapsed = cycle * 30
        batch = [("coverage", (timestamp, 2, 0, 0, 0.0))]
        for icao, callsign, x, track in (("a1", "ALPHA", -500 + elapsed * 10, 90),
                                         ("b2", "BRAVO", 500 - elapsed * 10, 270)):
            lon, lat = inverse.transform(x, 0)
            state = AircraftState(icao24=icao, callsign=callsign, latitude=lat, longitude=lon,
                                  baro_altitude_m=1100, geo_altitude_m=1000, velocity_mps=10,
                                  track_deg=track, vertical_rate_mps=0, on_ground=False,
                                  position_timestamp=timestamp, last_contact=timestamp)
            batch.append(("state", (timestamp, "opensky", state, [])))
        recorder._write_batch(batch)


def test_evaluation_is_deterministic_for_recorded_range(tmp_path):
    db_path = tmp_path / "recorded.db"
    recorded_database(db_path)
    config = yaml.safe_load(Path("config.yaml").read_text())
    first = asyncio.run(evaluate_recorded_range(db_path=db_path, start=1000, end=1060,
                                               config=config, profile="sensitive_test"))
    second = asyncio.run(evaluate_recorded_range(db_path=db_path, start=1000, end=1060,
                                                config=config, profile="sensitive_test"))
    assert first == second
    assert first["mode"] == "REPLAY"
    assert first["poll_cycles"] == 3
    assert first["pairs_evaluated"] >= 2
    assert first["events_count"] == 1
    assert first["events"][0]["status"] in {"ACTIVE", "RESOLVED"}
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    paths_a = write_evaluation_report(first, out_a)
    paths_b = write_evaluation_report(second, out_b)
    assert [path.read_text() for path in paths_a] == [path.read_text() for path in paths_b]
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM raw_states").fetchone()[0] == 6
