"""Lazy SQLite index for offline airport code/name/coordinate lookup."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

INDEX_VERSION = "2"


class AirportReferenceIndex:
    def __init__(self, csv_path: Path, database_path: Path):
        self.csv_path = Path(csv_path)
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.database_path)

    def _build_if_needed(self, db: sqlite3.Connection) -> None:
        if not self.csv_path.exists():
            raise FileNotFoundError(f"Airport reference CSV not found: {self.csv_path}; place OurAirports airports.csv there.")
        stamp = f"{INDEX_VERSION}:{self.csv_path.stat().st_size}:{self.csv_path.stat().st_mtime_ns}"
        db.execute("CREATE TABLE IF NOT EXISTS airport_reference_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        row = db.execute("SELECT value FROM airport_reference_meta WHERE key='source_stamp'").fetchone()
        if row and row[0] == stamp:
            return
        db.execute("DROP TABLE IF EXISTS airport_reference")
        db.execute("""CREATE TABLE airport_reference (
            ident TEXT, icao_code TEXT, iata_code TEXT, name TEXT NOT NULL, municipality TEXT,
            latitude REAL, longitude REAL, elevation_ft REAL)""")
        with self.csv_path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            db.executemany("""INSERT INTO airport_reference VALUES(?,?,?,?,?,?,?,?)""",
                ((r.get("ident", "").upper(), r.get("icao_code", "").upper(), r.get("iata_code", "").upper(), r.get("name", ""),
                  r.get("municipality") or None, _number(r.get("latitude_deg")), _number(r.get("longitude_deg")),
                  _number(r.get("elevation_ft"))) for r in reader))
        db.execute("CREATE INDEX airport_reference_icao ON airport_reference(icao_code)")
        db.execute("CREATE INDEX airport_reference_iata ON airport_reference(iata_code)")
        db.execute("CREATE INDEX airport_reference_ident ON airport_reference(ident)")
        db.execute("INSERT OR REPLACE INTO airport_reference_meta(key,value) VALUES('source_stamp',?)", (stamp,))
        db.commit()

    def lookup(self, code: str) -> dict | None:
        normalized = code.strip().upper()
        with self._connect() as db:
            self._build_if_needed(db)
            row = db.execute("""SELECT ident,icao_code,iata_code,name,municipality,latitude,longitude,elevation_ft
                FROM airport_reference WHERE icao_code=? OR ident=? OR iata_code=?
                ORDER BY CASE WHEN icao_code=? THEN 0 WHEN ident=? THEN 1 ELSE 2 END LIMIT 1""",
                (normalized, normalized, normalized, normalized, normalized)).fetchone()
        if row is None:
            return None
        return dict(zip(("ident", "icao_code", "iata_code", "name", "municipality", "latitude", "longitude", "elevation_ft"), row))


def _number(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
