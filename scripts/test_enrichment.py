#!/usr/bin/env python3
"""Measure adsbdb reference hit rates for callsigns in recorded observations."""
from __future__ import annotations
import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

import httpx
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.airwatch.airport_reference import AirportReferenceIndex
from backend.airwatch.enrichment import EnrichmentService


async def run(args):
    config=yaml.safe_load((ROOT/args.config).read_text(encoding="utf-8"))
    db=Path(args.db);db=db if db.is_absolute() else ROOT/db
    with sqlite3.connect(f"file:{db}?mode=ro",uri=True) as connection:
        rows=connection.execute("SELECT callsign,COUNT(*) AS n FROM raw_states WHERE callsign IS NOT NULL AND TRIM(callsign)!='' GROUP BY callsign ORDER BY n DESC,callsign LIMIT ?",(args.limit,)).fetchall()
        samples=[]
        for callsign,_count in rows:
            row=connection.execute("SELECT icao24 FROM raw_states WHERE callsign=? ORDER BY fetch_time DESC LIMIT 1",(callsign,)).fetchone()
            if row:samples.append((callsign,row[0]))
    settings=config["enrichment"]
    ref=config["airport_reference"]
    service=EnrichmentService(settings=settings,db_path=db,airport_index=AirportReferenceIndex(ROOT/ref["data_file"],ROOT/ref["database_file"]))
    airline_hits=route_hits=type_hits=0
    try:
        print(f"{'CALLSIGN':12} {'AIRLINE':24} {'FROM':18} {'TO':18} {'TYPE':10} SOURCE / STATUS")
        for callsign,icao24 in samples:
            value=await service.info(callsign=callsign,icao24=icao24)
            route=value.get("route",{});aircraft=value.get("aircraft",{})
            airline=value.get("airline") or "Unknown"
            origin=route.get("origin") or {};dest=route.get("destination") or {}
            from_label=origin.get("icao_code") or origin.get("iata_code") or "Unknown"
            to_label=dest.get("icao_code") or dest.get("iata_code") or "Unknown"
            type_name=aircraft.get("type") or "Unknown"
            airline_hits+=airline!="Unknown";route_hits+=bool(route.get("available"));type_hits+=bool(type_name!="Unknown")
            status="found" if airline!="Unknown" or route.get("available") or type_name!="Unknown" else "not found"
            print(f"{callsign[:12]:12} {airline[:24]:24} {from_label[:18]:18} {to_label[:18]:18} {type_name[:10]:10} {value.get('source','adsbdb')} / {status}")
        total=len(samples)
        print(f"\nCalls sampled: {total}")
        for label,count in (("airline",airline_hits),("route",route_hits),("aircraft type",type_hits)):
            print(f"{label} hit rate: {count}/{total} ({(100*count/total if total else 0):.1f}%)")
        print("The script reads raw_states through a read-only SQLite connection; route payloads are not persisted.")
    finally:
        await service.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit",type=int,default=25)
    parser.add_argument("--db",default="data/airwatch.db")
    parser.add_argument("--config",default="config.yaml")
    asyncio.run(run(parser.parse_args()))

if __name__=="__main__":main()
