#!/usr/bin/env python3
"""Inspect real adsbdb lookups. This script never persists provider payloads."""
import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml

from backend.airwatch.airport_reference import AirportReferenceIndex
from backend.airwatch.enrichment import EnrichmentService


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--callsign", help="Real callsign to query")
    parser.add_argument("--icao24", help="Real ICAO24 paired with --callsign")
    parser.add_argument("--recent", type=int, default=0, help="Use the N most recent distinct recorded callsigns")
    args = parser.parse_args()
    config = yaml.safe_load((ROOT / "config.yaml").read_text())
    settings = config["enrichment"]
    print(f"enrichment.enabled: {settings.get('enabled')}")
    if not settings.get("enabled"):
        print("Reason: enrichment is disabled in config.")
        return
    rows = []
    if args.callsign:
        rows = [(args.callsign, args.icao24)]
    elif args.recent:
        db_path = ROOT / config["storage"]["database_path"]
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
            rows = db.execute("SELECT callsign,icao24 FROM raw_states WHERE callsign IS NOT NULL "
                              "GROUP BY UPPER(TRIM(callsign)) ORDER BY MAX(fetch_time) DESC LIMIT ?", (args.recent,)).fetchall()
    else:
        parser.error("provide --callsign (optionally --icao24) or --recent N")
    settings = {**settings, "airlines_override_file": str(ROOT / settings["airlines_override_file"]),
                "airlines_file": str(ROOT / settings["airlines_file"])}
    index = AirportReferenceIndex(ROOT / config["airport_reference"]["data_file"],
                                  ROOT / config["airport_reference"]["database_file"])
    service = EnrichmentService(settings=settings, db_path=ROOT / "data/enrichment_metadata.sqlite", airport_index=index)
    try:
        for callsign, icao24 in rows:
            clean = " ".join((callsign or "").strip().split())
            print(f"\n=== {clean or 'Unknown callsign'} / {icao24 or 'no ICAO24 supplied'} ===")
            if not clean:
                print("Reason: callsign missing in recorded ADS-B state.")
                continue
            route = await service._fetch("callsign", clean, "callsign_ttl_s", "/callsign/"+__import__('urllib.parse').parse.quote(clean, safe=""))
            print("Callsign URL/status/JSON keys:", json.dumps(service._last_responses.get(("callsign", clean.upper()), {"reason":route.get("reason")}), indent=2))
            aircraft = None
            if icao24:
                aircraft = await service._fetch("aircraft", str(icao24), "aircraft_ttl_s", "/aircraft/"+str(icao24).strip())
                print("Aircraft URL/status/JSON keys:", json.dumps(service._last_responses.get(("aircraft", str(icao24).upper()), {"reason":aircraft.get("reason")}), indent=2))
            else:
                print("Aircraft details reason: aircraft was not looked up because no real ICAO24 was supplied.")
            result = await service.info(callsign=clean, icao24=str(icao24 or "")) if icao24 else {
                "callsign":clean,"airline":route.get("airline") or "Unknown",
                "route":route,"aircraft":{"available":False,"reason":"Aircraft was not looked up because ICAO24 was not supplied."}}
            print("Parsed airline:", result.get("airline"), "source:", result.get("airline_source"))
            print("Parsed route:", json.dumps(result.get("route"), ensure_ascii=False))
            print("Parsed aircraft:", json.dumps(result.get("aircraft"), ensure_ascii=False))
            for route_key in ("origin", "destination"):
                item=(result.get("route") or {}).get(route_key)
                code=(result.get("route") or {}).get(route_key+"_code")
                print(f"Airport reference {route_key} {code or 'no code'}:", json.dumps(index.lookup(code) if code else None, ensure_ascii=False))
            print("Final info payload:", json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
