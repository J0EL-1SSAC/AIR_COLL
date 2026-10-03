"""SQLite-backed DataSource that replays only observations recorded by this app."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from pyproj import Geod

from .models import AircraftState, AirportCenter

_NM_TO_M = 1852.0


class ReplaySource:
    """Replay successful poll cycles from coverage_samples and their raw_states rows."""
    def __init__(self, db_path: Path, *, start_time: float | None = None,
                 end_time: float | None = None):
        self.db_path = Path(db_path)
        with sqlite3.connect(self.db_path) as connection:
            sql = "SELECT sample_time FROM coverage_samples"
            where, params = [], []
            if start_time is not None:
                where.append("sample_time >= ?"); params.append(start_time)
            if end_time is not None:
                where.append("sample_time <= ?"); params.append(end_time)
            if where:
                sql += " WHERE " + " AND ".join(where)
            self._times = [float(row[0]) for row in connection.execute(sql + " ORDER BY sample_time", params)]
        self._index = 0
        self.next_fetch_time = self._times[0] if self._times else None
        self._cycle_time: float | None = None

    @property
    def total_cycles(self) -> int:
        return len(self._times)

    @property
    def finished(self) -> bool:
        return self._index >= len(self._times)

    async def fetch_states(self, center: AirportCenter, radius_nm: float) -> list[AircraftState]:
        if self.finished:
            self._cycle_time = None
            self.next_fetch_time = None
            return []
        cycle_time = self._times[self._index]
        self._index += 1
        self._cycle_time = cycle_time
        self.next_fetch_time = self._times[self._index] if not self.finished else None
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """SELECT icao24,callsign,latitude,longitude,baro_altitude_m,geo_altitude_m,
                          velocity_mps,track_deg,vertical_rate_mps,on_ground,position_timestamp,
                          last_contact,raw_payload_json
                   FROM raw_states WHERE fetch_time = ? AND source = 'opensky' ORDER BY id""",
                (cycle_time,),
            ).fetchall()
        geod = Geod(ellps="WGS84")
        result = []
        for row in rows:
            lat, lon = row[2], row[3]
            if lat is not None and lon is not None:
                _, _, distance_m = geod.inv(center.longitude, center.latitude, lon, lat)
                if distance_m > radius_nm * _NM_TO_M:
                    continue
            result.append(AircraftState(
                icao24=row[0], callsign=row[1], latitude=lat, longitude=lon,
                baro_altitude_m=row[4], geo_altitude_m=row[5], velocity_mps=row[6],
                track_deg=row[7], vertical_rate_mps=row[8],
                on_ground=None if row[9] is None else bool(row[9]),
                position_timestamp=row[10], last_contact=row[11], raw_payload_json=row[12],
            ))
        return result

    def current_cycle_time(self) -> float | None:
        return self._cycle_time
