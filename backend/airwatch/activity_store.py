"""SQLite persistence for explicitly inferred airport activity records."""
import json
import sqlite3


class AirportActivityStore:
    def __init__(self,path): self.path=path
    def initialize(self):
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS airport_activity (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL, dedupe_key TEXT NOT NULL,
                activity_type TEXT NOT NULL, icao24 TEXT NOT NULL, callsign TEXT, runway_end TEXT,
                time_ts REAL NOT NULL, inferred INTEGER NOT NULL, confidence TEXT NOT NULL,
                latitude REAL, longitude REAL, payload_json TEXT NOT NULL,
                UNIQUE(mode,dedupe_key))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_airport_activity_mode_time ON airport_activity(mode,time_ts)")
    def upsert(self,mode,item):
        key=f"{item['activity_type']}:{item['icao24']}:{item.get('runway_end')}:{item.get('time_ts')}"
        with sqlite3.connect(self.path) as db:
            db.execute("""INSERT OR IGNORE INTO airport_activity(mode,dedupe_key,activity_type,icao24,callsign,runway_end,
                time_ts,inferred,confidence,latitude,longitude,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (mode,key,item["activity_type"],item["icao24"],item.get("callsign"),item.get("runway_end"),item["time_ts"],
                 int(bool(item.get("inferred"))),item.get("confidence","LOW"),item.get("latitude"),item.get("longitude"),
                 json.dumps(item,sort_keys=True)))
    def query(self,mode="LIVE",start=None,end=None,limit=500):
        clauses=["mode=?"];params=[mode]
        if start is not None:clauses.append("time_ts>=?");params.append(float(start))
        if end is not None:clauses.append("time_ts<=?");params.append(float(end))
        params.append(int(limit))
        with sqlite3.connect(self.path) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT payload_json FROM airport_activity WHERE "+" AND ".join(clauses)+" ORDER BY time_ts DESC LIMIT ?",params)]
