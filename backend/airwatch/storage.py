from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from .models import AircraftState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time REAL NOT NULL,
    source TEXT NOT NULL,
    icao24 TEXT NOT NULL,
    callsign TEXT,
    latitude REAL,
    longitude REAL,
    baro_altitude_m REAL,
    geo_altitude_m REAL,
    velocity_mps REAL,
    track_deg REAL,
    vertical_rate_mps REAL,
    on_ground INTEGER,
    position_timestamp REAL,
    last_contact REAL,
    raw_payload_json TEXT,
    data_quality_flags TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_states_fetch_time ON raw_states(fetch_time);
CREATE INDEX IF NOT EXISTS idx_raw_states_icao_fetch ON raw_states(icao24, fetch_time);
CREATE TABLE IF NOT EXISTS coverage_samples (
    sample_time REAL PRIMARY KEY,
    aircraft_count INTEGER NOT NULL,
    below_threshold_count INTEGER NOT NULL,
    on_ground_count INTEGER NOT NULL,
    mean_data_age_s REAL
);
CREATE INDEX IF NOT EXISTS idx_coverage_sample_time ON coverage_samples(sample_time);
"""


class SQLiteRecorder:
    """Non-blocking enqueue with a background, batched SQLite WAL writer."""
    def __init__(self, db_path: Path, *, batch_size: int, flush_interval_s: float,
                 low_altitude_m: float, low_quality_after_s: float):
        self.db_path = db_path
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self.low_altitude_m = low_altitude_m
        self.low_quality_after_s = low_quality_after_s
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        await asyncio.to_thread(self._initialize)
        self._task = asyncio.create_task(self._writer(), name="sqlite-recorder")

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path, timeout=30) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(raw_states)")}
            if "raw_payload_json" not in columns:
                connection.execute("ALTER TABLE raw_states ADD COLUMN raw_payload_json TEXT")

    @staticmethod
    def quality_flags(state: AircraftState, fetch_time: float, low_quality_after_s: float) -> list[str]:
        flags = []
        if state.latitude is None or state.longitude is None:
            flags.append("position_missing")
        if state.baro_altitude_m is None:
            flags.append("altitude_missing")
        if state.velocity_mps is None or state.track_deg is None:
            flags.append("velocity_missing")
        if state.on_ground is None:
            flags.append("on_ground_ambiguous")
        if state.position_timestamp is None:
            flags.append("position_timestamp_missing")
        elif max(0.0, fetch_time - state.position_timestamp) >= low_quality_after_s:
            flags.extend(["stale_position", "LOW_QUALITY"])
        return flags

    async def record_batch(self, states: Iterable[AircraftState], fetch_time: float, source: str) -> None:
        # Queue insertion never waits on disk I/O; each sampled state is retained for the writer.
        states = list(states)
        below = sum(1 for state in states if state.baro_altitude_m is not None and state.baro_altitude_m < self.low_altitude_m)
        ground = sum(1 for state in states if state.on_ground is True)
        ages = [max(0.0, fetch_time - state.position_timestamp) for state in states if state.position_timestamp is not None]
        self._queue.put_nowait(("coverage", (fetch_time, len(states), below, ground,
                                             sum(ages) / len(ages) if ages else None)))
        for state in states:
            self._queue.put_nowait(("state", (fetch_time, source, state,
                                               self.quality_flags(state, fetch_time, self.low_quality_after_s))))

    async def _writer(self) -> None:
        batch: list[tuple] = []
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=self.flush_interval_s)
            except asyncio.TimeoutError:
                item = None
            if item is not None:
                if item is _STOP:
                    if batch:
                        await asyncio.to_thread(self._write_batch, batch)
                    return
                batch.append(item)
                while len(batch) < self.batch_size:
                    try:
                        queued = self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if queued is _STOP:
                        await asyncio.to_thread(self._write_batch, batch)
                        return
                    batch.append(queued)
            if batch and (len(batch) >= self.batch_size or item is None):
                await asyncio.to_thread(self._write_batch, batch)
                batch.clear()

    def _write_batch(self, batch: list[tuple]) -> None:
        sql = """INSERT INTO raw_states
        (fetch_time,source,icao24,callsign,latitude,longitude,baro_altitude_m,geo_altitude_m,
         velocity_mps,track_deg,vertical_rate_mps,on_ground,position_timestamp,last_contact,raw_payload_json,data_quality_flags)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        state_rows = []
        coverage_rows = []
        for kind, payload in batch:
            if kind == "state":
                fetch, source, state, flags = payload
                state_rows.append((fetch, source, state.icao24, state.callsign, state.latitude, state.longitude,
                                   state.baro_altitude_m, state.geo_altitude_m, state.velocity_mps, state.track_deg,
                                   state.vertical_rate_mps, None if state.on_ground is None else int(state.on_ground),
                                   state.position_timestamp, state.last_contact, state.raw_payload_json, json.dumps(flags)))
            elif kind == "coverage":
                coverage_rows.append(payload)
        with self._connect() as connection:
            if state_rows:
                connection.executemany(sql, state_rows)
            if coverage_rows:
                connection.executemany("INSERT OR REPLACE INTO coverage_samples VALUES (?,?,?,?,?)", coverage_rows)

    async def stop(self) -> None:
        if self._task is None or self._stopping:
            return
        self._stopping = True
        await self._queue.put(_STOP)
        await self._task
        self._task = None

    def coverage(self, *, since: float, bucket_s: int) -> dict:
        sql = """SELECT CAST(sample_time / ? AS INTEGER) * ? AS bucket,
                 MAX(aircraft_count), MAX(below_threshold_count), MAX(on_ground_count), AVG(mean_data_age_s)
                 FROM coverage_samples WHERE sample_time >= ? GROUP BY bucket ORDER BY bucket"""
        with self._connect() as connection:
            rows = connection.execute(sql, (bucket_s, bucket_s, since)).fetchall()
        samples = [{"bucket_start": row[0], "aircraft_count": row[1], "below_threshold_count": row[2],
                    "on_ground_count": row[3], "mean_data_age_s": row[4]} for row in rows]
        return {"since": since, "bucket_s": bucket_s, "samples": samples}


_STOP = object()
