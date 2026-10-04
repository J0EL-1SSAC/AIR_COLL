"""Optional reference enrichment. Provider payloads are cached in memory only.

adsbdb route data carries a published restriction against incorporating it into
other databases. SQLite therefore stores request metadata only, never payloads.
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from .airport_reference import AirportReferenceIndex

logger = logging.getLogger(__name__)


class EnrichmentService:
    def __init__(self, *, settings: dict, db_path: Path, airport_index: AirportReferenceIndex,
                 client=None, monotonic=time.monotonic, wall_time=time.time):
        self.settings = settings
        self.db_path = Path(db_path)
        self.airport_index = airport_index
        self.client = client or httpx.AsyncClient(timeout=float(settings["timeout_s"]))
        self._owns_client = client is None
        self._monotonic, self._wall_time = monotonic, wall_time
        self._lock = asyncio.Lock()
        self._request_times: list[float] = []
        self._memory: dict[tuple[str, str], tuple[float, dict]] = {}
        self._retry_after: dict[tuple[str, str], float] = {}
        self._initialize()

    def _initialize(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS enrichment_cache (
                entity_type TEXT NOT NULL, lookup_key TEXT NOT NULL, fetched_at REAL NOT NULL,
                source TEXT NOT NULL, negative_cache INTEGER NOT NULL, PRIMARY KEY(entity_type,lookup_key))""")

    async def _rate_limit(self):
        async with self._lock:
            now = self._monotonic()
            window = float(self.settings["rate_window_s"])
            self._request_times = [item for item in self._request_times if now-item < window]
            maximum = int(self.settings["max_requests_per_window"])
            if len(self._request_times) >= maximum:
                delay = window-(now-self._request_times[0])
                await asyncio.sleep(max(0.0, delay))
                now = self._monotonic()
                self._request_times = [item for item in self._request_times if now-item < window]
            self._request_times.append(self._monotonic())

    def _metadata(self, kind: str, key: str, negative: bool):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT OR REPLACE INTO enrichment_cache VALUES(?,?,?,?,?)",
                       (kind, key, self._wall_time(), "adsbdb", int(negative)))

    async def _fetch(self, kind: str, key: str, ttl_key: str, path: str) -> dict:
        normalized = key.strip().upper()
        cache_key = (kind, normalized)
        now = self._monotonic()
        cached = self._memory.get(cache_key)
        if cached and now-cached[0] < float(self.settings[ttl_key]):
            return dict(cached[1])
        if now < self._retry_after.get(cache_key, 0):
            return {"available": False, "reason": "Provider retry backoff is active.", "source": "adsbdb"}
        await self._rate_limit()
        try:
            response = await self.client.get(self.settings["base_url"].rstrip("/")+path)
            response.raise_for_status()
            payload = response.json()
            result = self._parse(kind, payload)
            self._memory[cache_key] = (self._monotonic(), result)
            self._metadata(kind, normalized, not result.get("available", False))
            self._retry_after.pop(cache_key, None)
            return dict(result)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
            logger.info("adsbdb %s lookup unavailable for %s: %s", kind, normalized, error)
            self._metadata(kind, normalized, True)
            self._retry_after[cache_key] = self._monotonic()+float(self.settings["failure_backoff_s"])
            return {"available": False, "reason": "Reference lookup unavailable.", "source": "adsbdb"}

    @staticmethod
    def _parse(kind: str, payload: dict) -> dict:
        root = payload.get("response") or {}
        if kind == "callsign":
            route = root.get("flightroute") or {}
            airline = route.get("airline") or {}
            origin, destination = route.get("origin") or {}, route.get("destination") or {}
            return {"available": bool(route), "source": "adsbdb", "airline": airline.get("name"),
                    "origin_code": origin.get("icao_code") or origin.get("iata_code"),
                    "destination_code": destination.get("icao_code") or destination.get("iata_code"),
                    "route_label": "Reported route (callsign database)",
                    "attribution": "Route reference: adsbdb; route data credits David Taylor and Jim Mason."}
        aircraft = root.get("aircraft") or {}
        return {"available": bool(aircraft), "source": "adsbdb",
                "registration": aircraft.get("registration"), "type": aircraft.get("type"),
                "operator": aircraft.get("registered_owner") or aircraft.get("registered_owner_country_name")}

    async def info(self, *, callsign: str, icao24: str) -> dict:
        clean_callsign = " ".join((callsign or "").strip().split())
        route = await self._fetch("callsign", clean_callsign, "callsign_ttl_s", "/callsign/"+quote(clean_callsign, safe="")) if clean_callsign else {"available":False,"reason":"Callsign unavailable.","source":"adsbdb"}
        if not route.get("airline") and clean_callsign:
            route["airline"] = self._airline_fallback(clean_callsign[:3])
        aircraft = await self._fetch("aircraft", icao24, "aircraft_ttl_s", "/aircraft/"+quote(icao24.strip(), safe=""))
        for key in ("origin_code", "destination_code"):
            code=route.get(key)
            route[key.removesuffix("_code")] = self._lookup_airport(code) if code else None
        return {"callsign": clean_callsign or None, "airline": route.get("airline") or "Unknown",
                "route": route if route.get("available") else {"available":False,"label":"Route unavailable","reason":route.get("reason","No callsign route record."),"source":"adsbdb"},
                "aircraft": aircraft if aircraft.get("available") else {"available":False,"reason":aircraft.get("reason","No aircraft reference record."),"source":"adsbdb"},
                "source": "adsbdb reference database", "route_data_persistence": "memory only; SQLite stores cache metadata only",
                "attribution": "Aircraft and route reference lookups from adsbdb; aircraft data credits PlaneBase and route data credits David Taylor and Jim Mason. Route records may not be incorporated into other databases without permission."}

    def _lookup_airport(self, code):
        try:
            return self.airport_index.lookup(code)
        except (FileNotFoundError, OSError, sqlite3.Error):
            return {"code":code,"available":False,"reason":"Local airports.csv reference unavailable."}

    def _airline_fallback(self, icao_prefix: str):
        filename=self.settings.get("airlines_file")
        if not filename or not Path(filename).exists():
            return None
        try:
            with Path(filename).open(newline="",encoding="utf-8-sig") as stream:
                for row in csv.reader(stream):
                    # OpenFlights airlines.dat: ID, name, alias, IATA, ICAO, callsign, country, active.
                    if len(row)>4 and row[4].strip().upper()==icao_prefix.strip().upper():
                        return row[1].strip() or None
        except OSError:
            return None
        return None

    async def close(self):
        if self._owns_client:
            await self.client.aclose()
