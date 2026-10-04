"""Persistence for LIVE/REPLAY approach tracks; independent of raw observation recording."""
import json
import sqlite3


class SQLiteApproachStore:
    def __init__(self, db_path):
        self.db_path = db_path

    def initialize(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS approach_tracks (
                approach_id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL, icao24 TEXT NOT NULL,
                callsign TEXT, runway_end TEXT NOT NULL, state TEXT NOT NULL, first_seen_ts REAL NOT NULL,
                last_seen_ts REAL NOT NULL, samples INTEGER NOT NULL, min_altitude_m REAL,
                last_distance_to_threshold_m REAL, outcome TEXT, latest_json TEXT NOT NULL,
                UNIQUE(mode, icao24, runway_end, first_seen_ts))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_approach_mode_time ON approach_tracks(mode, first_seen_ts)")

    def upsert(self, mode: str, item: dict):
        with sqlite3.connect(self.db_path) as db:
            key = item["icao24"], item["runway_end"], item["first_seen_ts"]
            db.execute("""INSERT INTO approach_tracks(mode,icao24,callsign,runway_end,state,first_seen_ts,last_seen_ts,
                samples,min_altitude_m,last_distance_to_threshold_m,outcome,latest_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(mode,icao24,runway_end,first_seen_ts) DO UPDATE SET
                state=excluded.state,last_seen_ts=excluded.last_seen_ts,samples=excluded.samples,
                min_altitude_m=CASE WHEN min_altitude_m IS NULL THEN excluded.min_altitude_m
                                    WHEN excluded.min_altitude_m IS NULL THEN min_altitude_m
                                    ELSE MIN(min_altitude_m,excluded.min_altitude_m) END,
                last_distance_to_threshold_m=excluded.last_distance_to_threshold_m,outcome=excluded.outcome,
                latest_json=excluded.latest_json""", (mode, key[0], item.get("callsign"), key[1], item["state"], key[2],
                    item["last_seen_ts"], item["samples"], item.get("altitude_m"),
                    item.get("distance_to_threshold_m"), item.get("outcome"), json.dumps(item, allow_nan=False)))

    def query(self, mode="LIVE", start=None, end=None):
        sql = "SELECT latest_json FROM approach_tracks WHERE mode=?"
        params = [mode]
        if start is not None:
            sql += " AND last_seen_ts>=?"; params.append(start)
        if end is not None:
            sql += " AND first_seen_ts<=?"; params.append(end)
        sql += " ORDER BY first_seen_ts"
        with sqlite3.connect(self.db_path) as db:
            return [json.loads(row[0]) for row in db.execute(sql, params)]
