"""SQLite persistence and querying for alert lifecycle events."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Iterable, Mapping

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
 event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, status TEXT NOT NULL,
 mode TEXT NOT NULL, risk_profile TEXT NOT NULL, airport TEXT NOT NULL,
 alert_kind TEXT NOT NULL,
 aircraft_1_icao24 TEXT NOT NULL, aircraft_1_callsign TEXT, aircraft_2_icao24 TEXT NOT NULL,
 aircraft_2_callsign TEXT, pair_key TEXT NOT NULL, first_seen_ts REAL NOT NULL,
 last_updated_ts REAL NOT NULL, opened_ts REAL, resolved_ts REAL, resolution_reason TEXT,
 current_risk TEXT NOT NULL, peak_risk TEXT NOT NULL, h_sep_cpa_m REAL,
 min_h_sep_cpa_m REAL, v_sep_cpa_m REAL, min_v_sep_cpa_m REAL,
 time_to_cpa_s REAL, min_time_to_cpa_s REAL, closing_speed_mps REAL,
 altitude_basis TEXT, data_age_a_s REAL, data_age_b_s REAL,
 reasons_json TEXT NOT NULL, score_components_json TEXT NOT NULL, confidence TEXT NOT NULL,
 aircraft_states_first_json TEXT, aircraft_states_peak_json TEXT,
 cpa_lat REAL, cpa_lon REAL, cpa_aircraft_a_lat REAL, cpa_aircraft_a_lon REAL,
 cpa_aircraft_b_lat REAL, cpa_aircraft_b_lon REAL, cycles_observed INTEGER NOT NULL,
 confirmation_elapsed_s REAL NOT NULL, confirmation_started_ts REAL NOT NULL,
 confirmation_cycles INTEGER NOT NULL, continuation_count INTEGER NOT NULL,
 duration_s REAL NOT NULL,
 last_seen_ts REAL NOT NULL, below_threshold_since_ts REAL,
 below_threshold_cycles INTEGER NOT NULL, missing_since_ts REAL
);
CREATE INDEX IF NOT EXISTS idx_events_mode_status ON events(mode,status,last_updated_ts);
CREATE INDEX IF NOT EXISTS idx_events_risk_mode ON events(current_risk,mode,last_updated_ts);
CREATE INDEX IF NOT EXISTS idx_events_pair_time ON events(pair_key,first_seen_ts);
CREATE INDEX IF NOT EXISTS idx_events_aircraft1_time ON events(aircraft_1_icao24,first_seen_ts);
CREATE INDEX IF NOT EXISTS idx_events_aircraft2_time ON events(aircraft_2_icao24,first_seen_ts);
CREATE INDEX IF NOT EXISTS idx_events_resolved_time ON events(resolved_ts);
"""

_COLUMNS = (
    "event_id event_type status mode risk_profile airport alert_kind aircraft_1_icao24 aircraft_1_callsign "
    "aircraft_2_icao24 aircraft_2_callsign pair_key first_seen_ts last_updated_ts opened_ts resolved_ts "
    "resolution_reason current_risk peak_risk h_sep_cpa_m min_h_sep_cpa_m v_sep_cpa_m "
    "min_v_sep_cpa_m time_to_cpa_s min_time_to_cpa_s closing_speed_mps altitude_basis "
    "data_age_a_s data_age_b_s reasons_json score_components_json "
    "confidence aircraft_states_first_json aircraft_states_peak_json cpa_lat cpa_lon "
    "cpa_aircraft_a_lat cpa_aircraft_a_lon cpa_aircraft_b_lat cpa_aircraft_b_lon cycles_observed "
    "confirmation_elapsed_s confirmation_started_ts confirmation_cycles continuation_count "
    "duration_s last_seen_ts below_threshold_since_ts "
    "below_threshold_cycles missing_since_ts"
).split()

_MIGRATIONS = {
    "alert_kind": "TEXT NOT NULL DEFAULT 'ALERT'",
    "opened_ts": "REAL",
    "data_age_a_s": "REAL",
    "data_age_b_s": "REAL",
    "score_components_json": "TEXT NOT NULL DEFAULT '{}'",
    "cpa_aircraft_a_lat": "REAL",
    "cpa_aircraft_a_lon": "REAL",
    "cpa_aircraft_b_lat": "REAL",
    "cpa_aircraft_b_lon": "REAL",
    "confirmation_started_ts": "REAL NOT NULL DEFAULT 0",
    "confirmation_cycles": "INTEGER NOT NULL DEFAULT 0",
    "duration_s": "REAL NOT NULL DEFAULT 0",
}


class SQLiteEventStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=30) as connection:
            connection.executescript(_SCHEMA)
            existing = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
            for column, declaration in _MIGRATIONS.items():
                if column not in existing:
                    connection.execute(f"ALTER TABLE events ADD COLUMN {column} {declaration}")

    @staticmethod
    def _db_values(event: Mapping) -> tuple:
        aircraft_1, aircraft_2 = event["aircraft_1"], event["aircraft_2"]
        values = dict(event)
        values.update({"aircraft_1_icao24": aircraft_1["icao24"],
                       "aircraft_1_callsign": aircraft_1.get("callsign"),
                       "aircraft_2_icao24": aircraft_2["icao24"],
                       "aircraft_2_callsign": aircraft_2.get("callsign"),
                       "reasons_json": json.dumps(event.get("reasons", []), sort_keys=True),
                       "score_components_json": json.dumps(event.get("score_components", {}), sort_keys=True),
                       "aircraft_states_first_json": json.dumps(event.get("aircraft_states_first"), sort_keys=True),
                       "aircraft_states_peak_json": json.dumps(event.get("aircraft_states_peak"), sort_keys=True)})
        return tuple(values.get(column) for column in _COLUMNS)

    async def upsert_many(self, events: Iterable[Mapping]) -> None:
        events = list(events)
        if not events:
            return
        placeholders = ",".join("?" for _ in _COLUMNS)
        updates = ",".join(f"{column}=excluded.{column}" for column in _COLUMNS if column != "event_id")
        sql = (f"INSERT INTO events ({','.join(_COLUMNS)}) VALUES ({placeholders}) "
               f"ON CONFLICT(event_id) DO UPDATE SET {updates}")
        rows = [self._db_values(event) for event in events]
        await asyncio.to_thread(self._write_rows, sql, rows)

    def _write_rows(self, sql: str, rows: list[tuple]) -> None:
        with sqlite3.connect(self.path, timeout=30) as connection:
            connection.executemany(sql, rows)

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["aircraft_1"] = {"icao24": item.pop("aircraft_1_icao24"), "callsign": item.pop("aircraft_1_callsign")}
        item["aircraft_2"] = {"icao24": item.pop("aircraft_2_icao24"), "callsign": item.pop("aircraft_2_callsign")}
        item["reasons"] = json.loads(item.pop("reasons_json"))
        item["score_components"] = json.loads(item.pop("score_components_json") or "{}")
        item["aircraft_states_first"] = json.loads(item.pop("aircraft_states_first_json") or "null")
        item["aircraft_states_peak"] = json.loads(item.pop("aircraft_states_peak_json") or "null")
        item["duration_s"] = max(float(item.get("duration_s") or 0.0),
                                  max(0.0, float(item.get("resolved_ts") or item["last_updated_ts"]) - float(item["first_seen_ts"])))
        return item

    def get(self, event_id: str) -> dict | None:
        with sqlite3.connect(self.path, timeout=30) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        return None if row is None else self._decode(row)

    def query(self, *, status: str | None = None, risk: str | None = None,
              mode: str | None = "LIVE", aircraft: str | None = None,
              start_ts: float | None = None, end_ts: float | None = None,
              limit: int = 100, offset: int = 0) -> list[dict]:
        clauses, params = [], []
        if mode:
            clauses.append("mode=?"); params.append(mode)
        if status:
            clauses.append("status=?"); params.append(status.upper())
        if risk:
            clauses.append("(current_risk=? OR peak_risk=?)"); params.extend((risk.upper(), risk.upper()))
        if aircraft:
            clauses.append("(lower(aircraft_1_icao24)=lower(?) OR lower(aircraft_2_icao24)=lower(?))")
            params.extend((aircraft, aircraft))
        if start_ts is not None:
            clauses.append("first_seen_ts>=?"); params.append(float(start_ts))
        if end_ts is not None:
            clauses.append("first_seen_ts<=?"); params.append(float(end_ts))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (f"SELECT * FROM events{where} "
               "ORDER BY first_seen_ts DESC,event_id LIMIT ? OFFSET ?")
        params.extend((max(0, int(limit)), max(0, int(offset))))
        with sqlite3.connect(self.path, timeout=30) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(sql, params).fetchall()
        return [self._decode(row) for row in rows]

    def counts_by_mode(self) -> dict[str, int]:
        with sqlite3.connect(self.path, timeout=30) as connection:
            rows = connection.execute("SELECT mode,COUNT(*) FROM events GROUP BY mode").fetchall()
        return {str(mode): int(count) for mode, count in rows}

    def active(self, *, mode: str = "LIVE") -> list[dict]:
        return self._active(mode)

    def recent(self, *, mode: str, risk_profile: str, limit: int = 1000) -> list[dict]:
        with sqlite3.connect(self.path, timeout=30) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM events WHERE mode=? AND risk_profile=? ORDER BY last_updated_ts DESC LIMIT ?",
                (mode, risk_profile, max(0, int(limit)))).fetchall()
        return [self._decode(row) for row in rows]

    def _active(self, mode: str) -> list[dict]:
        with sqlite3.connect(self.path, timeout=30) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM events WHERE mode=? AND status IN ('CANDIDATE','ACTIVE','ESCALATED') "
                "ORDER BY CASE current_risk WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 "
                "WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 3 ELSE 4 END,time_to_cpa_s,first_seen_ts",
                (mode,)).fetchall()
        return [self._decode(row) for row in rows]
