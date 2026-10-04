"""Optional reference enrichment. Provider payloads are cached in memory only.

adsbdb route data carries a published restriction against incorporating it into
other databases. SQLite therefore stores request metadata only, never payloads.
"""
from __future__ import annotations

import asyncio
import csv
import yaml
import heapq
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
        self._queue: list[tuple[int, int, str, str]] = []
        self._queued: set[str] = set()
        self._inflight: set[str] = set()
        self._sequence = 0
        self._worker: asyncio.Task | None = None
        self._by_icao: dict[str, dict] = {}
        self._callsign_by_icao: dict[str, str] = {}
        self._last_responses: dict[tuple[str, str], dict] = {}
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
            request_url = self.settings["base_url"].rstrip("/")+path
            response = await self.client.get(request_url)
            diagnostic = {"url": str(getattr(response, "url", request_url)), "http_status": response.status_code}
            response.raise_for_status()
            payload = response.json()
            diagnostic["raw_keys"] = sorted(payload.keys()) if isinstance(payload, dict) else []
            diagnostic["raw_json"] = payload
            self._last_responses[cache_key] = diagnostic
            result = self._parse(kind, payload)
            self._memory[cache_key] = (self._monotonic(), result)
            self._metadata(kind, normalized, not result.get("available", False))
            self._retry_after.pop(cache_key, None)
            return dict(result)
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            self._last_responses[cache_key] = {"url": str(error.request.url), "http_status": status}
            reason = f"Provider returned HTTP {status}."
            try:
                response_payload = error.response.json()
                if isinstance(response_payload.get("response"), str):
                    reason = response_payload["response"]
            except (ValueError, AttributeError):
                pass
            logger.info("adsbdb %s lookup unavailable for %s: %s", kind, normalized, reason)
            result = {"available": False, "reason": reason, "source": "adsbdb", "http_status": status}
            self._memory[cache_key] = (self._monotonic(), result)
            self._metadata(kind, normalized, True)
            self._retry_after[cache_key] = self._monotonic()+float(self.settings["failure_backoff_s"])
            return result
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
            logger.info("adsbdb %s lookup unavailable for %s: %s", kind, normalized, error)
            self._metadata(kind, normalized, True)
            self._retry_after[cache_key] = self._monotonic()+float(self.settings["failure_backoff_s"])
            return {"available": False, "reason": "Reference lookup unavailable.", "source": "adsbdb"}

    @staticmethod
    def _parse(kind: str, payload: dict) -> dict:
        root = payload.get("response") or {}
        if not isinstance(root, dict):
            return {"available": False, "source": "adsbdb",
                    "reason": f"Provider returned a non-record response: {root!s}"}
        if kind == "callsign":
            route = root.get("flightroute") or {}
            airline = route.get("airline") or {}
            origin, destination = route.get("origin") or {}, route.get("destination") or {}
            if not route:
                return {"available": False, "source": "adsbdb", "reason": "Callsign is not present in the callsign database."}
            return {"available": True, "source": "adsbdb", "airline": airline.get("name"),
                    "airline_source": "adsbdb flightroute" if airline.get("name") else None,
                    "origin_code": origin.get("icao_code") or origin.get("iata_code"),
                    "destination_code": destination.get("icao_code") or destination.get("iata_code"),
                    "origin_provider": origin, "destination_provider": destination,
                    "route_label": "Reported route (callsign database)",
                    "attribution": "Route reference: adsbdb; route data credits David Taylor and Jim Mason."}
        aircraft = root.get("aircraft") or {}
        if not isinstance(aircraft, dict) or not aircraft:
            return {"available": False, "source": "adsbdb", "reason": "Aircraft is not present in the Mode S reference database."}
        return {"available": True, "source": "adsbdb", "registration": aircraft.get("registration"),
                "type": aircraft.get("type"), "icao_type": aircraft.get("icao_type"),
                "manufacturer": aircraft.get("manufacturer"),
                "operator": aircraft.get("registered_owner") or aircraft.get("registered_owner_country_name"),
                "photo_url": aircraft.get("url_photo_thumbnail") or aircraft.get("url_photo"),
                "photo_source": "airport-data.com via adsbdb" if (aircraft.get("url_photo_thumbnail") or aircraft.get("url_photo")) else None,
                "photo_attribution": "Aircraft photo credited to airport-data.com by the adsbdb README; hotlinked, not stored."}

    async def info(self, *, callsign: str, icao24: str) -> dict:
        clean_callsign = " ".join((callsign or "").strip().split())
        route = await self._fetch("callsign", clean_callsign, "callsign_ttl_s", "/callsign/"+quote(clean_callsign, safe="")) if clean_callsign else {"available":False,"reason":"Callsign unavailable.","source":"adsbdb"}
        if not route.get("airline") and clean_callsign:
            fallback = self._airline_fallback(clean_callsign[:3])
            if fallback:
                route["airline"], route["airline_source"] = fallback["name"], fallback["source"]
        aircraft = await self._fetch("aircraft", icao24, "aircraft_ttl_s", "/aircraft/"+quote(icao24.strip(), safe=""))
        if aircraft.get("available") and aircraft.get("type"):
            aircraft["display_type"] = (f"{aircraft.get('manufacturer')} {aircraft['type']} ({aircraft['icao_type']})"
                                         if aircraft.get("manufacturer") and aircraft.get("icao_type")
                                         else f"{aircraft['type']} ({aircraft['icao_type']})" if aircraft.get("icao_type")
                                         else aircraft["type"])
        for key in ("origin_code", "destination_code"):
            code=route.get(key)
            label=key.removesuffix("_code")
            route[label] = (self._lookup_airport(code) or route.get(f"{label}_provider")) if code else None
        result = {"callsign": clean_callsign or None, "airline": route.get("airline") or "Unknown",
                "airline_source": route.get("airline_source") or ("Unknown · no matching source record"),
                "route": route if route.get("available") else {"available":False,"label":"Route unavailable","reason":route.get("reason","No callsign route record."),"source":"adsbdb"},
                "aircraft": aircraft if aircraft.get("available") else {"available":False,"reason":aircraft.get("reason","No aircraft reference record."),"source":"adsbdb"},
                "route_age_s": None if not clean_callsign or ("callsign",clean_callsign.upper()) not in self._memory else max(0.0,self._monotonic()-self._memory[("callsign",clean_callsign.upper())][0]),
                "aircraft_age_s": None if ("aircraft",icao24.strip().upper()) not in self._memory else max(0.0,self._monotonic()-self._memory[("aircraft",icao24.strip().upper())][0]),
                "source": "adsbdb reference database", "route_data_persistence": "memory only; SQLite stores cache metadata only",
                "attribution": "Aircraft and route reference lookups from adsbdb; aircraft data credits PlaneBase and route data credits David Taylor and Jim Mason. Aircraft photo credits airport-data.com. Route records may not be incorporated into other databases without permission."}
        self._by_icao[icao24.strip().upper()] = result
        self._callsign_by_icao[icao24.strip().upper()] = clean_callsign
        return result

    @staticmethod
    def queue_priority(*, selected: bool, approaching_or_departing: bool) -> int:
        return 0 if selected else 1 if approaching_or_departing else 2

    def start_background(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_queue())

    def enqueue(self, *, callsign: str, icao24: str, priority: int = 2) -> None:
        clean = " ".join((callsign or "").strip().split())
        mode_s = (icao24 or "").strip().upper()
        if not self.settings.get("enabled", False) or not mode_s or not clean:
            return
        key = f"{mode_s}:{clean.upper()}"
        cached = self._by_icao.get(mode_s)
        if key in self._queued or (cached and cached.get("callsign") == clean):
            return
        self._sequence += 1
        heapq.heappush(self._queue, (int(priority), self._sequence, clean, mode_s))
        self._queued.add(key)
        self.start_background()

    async def _run_queue(self) -> None:
        while self._queue:
            priority, sequence, callsign, mode_s = heapq.heappop(self._queue)
            key = f"{mode_s}:{callsign.upper()}"
            self._inflight.add(mode_s)
            try:
                await self.info(callsign=callsign, icao24=mode_s)
            except Exception:
                logger.exception("Background enrichment failed for %s", mode_s)
            finally:
                self._queued.discard(key)
                self._inflight.discard(mode_s)

    def cached_for_states(self, states: list[dict]) -> dict:
        output = {}
        for state in states:
            mode_s = str(state.get("icao24", "")).upper()
            info = self._by_icao.get(mode_s)
            output[mode_s] = {"status": "AVAILABLE" if info else "LOOKING_UP" if mode_s in self._inflight or any(item[3] == mode_s for item in self._queue) else "UNKNOWN",
                              "info": info,
                              "reason": None if info else "Waiting for prioritized reference lookup."}
        return output

    def _lookup_airport(self, code):
        try:
            return self.airport_index.lookup(code)
        except (FileNotFoundError, OSError, sqlite3.Error):
            return {"code":code,"available":False,"reason":"Local airports.csv reference unavailable."}

    def _airline_fallback(self, icao_prefix: str):
        override_file = self.settings.get("airlines_override_file")
        if override_file and Path(override_file).exists():
            try:
                data = yaml.safe_load(Path(override_file).read_text(encoding="utf-8")) or {}
                entry = (data.get("airlines") or {}).get(icao_prefix.strip().upper())
                if entry and entry.get("name"):
                    return {"name": entry["name"], "source": entry.get("source", "local airlines override")}
            except (OSError, yaml.YAMLError, AttributeError):
                logger.warning("Airline override file could not be parsed: %s", override_file)
        filename=self.settings.get("airlines_file")
        if not filename or not Path(filename).exists():
            return None
        try:
            with Path(filename).open(newline="",encoding="utf-8-sig") as stream:
                for row in csv.reader(stream):
                    # OpenFlights airlines.dat: ID, name, alias, IATA, ICAO, callsign, country, active.
                    if len(row)>4 and row[4].strip().upper()==icao_prefix.strip().upper():
                        return {"name": row[1].strip(), "source": "OpenFlights airlines.dat"} if row[1].strip() else None
        except OSError:
            return None
        return None

    async def close(self):
        if self._worker and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        if self._owns_client:
            await self.client.aclose()
