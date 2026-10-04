"""Live VOMM METAR/TAF retrieval and explicit availability reporting."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def utc_iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def select_metar(rows: Any) -> dict[str, Any] | None:
    """Select the newest provider record for the requested station."""
    if not isinstance(rows, list):
        return None
    valid = [row for row in rows if isinstance(row, dict) and row.get("rawOb")]
    if not valid:
        return None
    return max(valid, key=lambda row: float(row.get("obsTime", 0) or 0))


def select_taf(rows: Any) -> dict[str, Any] | None:
    if not isinstance(rows, list):
        return None
    valid = [row for row in rows if isinstance(row, dict) and row.get("rawTAF")]
    if not valid:
        return None
    return max(valid, key=lambda row: float(row.get("issueTime", 0) or 0))


class LiveWeatherService:
    """Small cache refreshed from AviationWeather.gov, with no synthetic fallback."""

    def __init__(self, *, airport: str, settings: dict[str, Any]):
        self.airport = airport.upper()
        self.settings = settings
        self._client: httpx.AsyncClient | None = None
        self._metar: dict[str, Any] = {"status": "CHECKING", "report": None, "error": None,
                                       "fetched_at": None}
        self._taf: dict[str, Any] = {"status": "CHECKING", "report": None, "error": None,
                                     "fetched_at": None}
        self._last_taf_attempt = 0.0

    async def _fetch_product(self, key: str, url: str, selector) -> None:
        now = time.time()
        previous = getattr(self, f"_{key}")
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=float(self.settings["request_timeout_s"]),
                    headers={"User-Agent": self.settings["user_agent"], "Accept": "application/json"})
            response = await self._client.get(url, params={"ids": self.airport, "format": "json"})
            if response.status_code == 204:
                setattr(self, f"_{key}", {"status": "NOT_AVAILABLE", "report": None, "error": None,
                                           "fetched_at": now})
                return
            response.raise_for_status()
            record = selector(response.json())
            setattr(self, f"_{key}", {"status": "AVAILABLE" if record else "NOT_AVAILABLE",
                                       "report": record, "error": None, "fetched_at": now})
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            setattr(self, f"_{key}", {**previous,
                "status": "DEGRADED" if previous.get("report") else "NOT_AVAILABLE",
                "error": f"Live provider request failed: {exc}", "last_attempt_at": now})
            logger.warning("Live %s unavailable for %s: %s", key, self.airport, exc)

    async def refresh(self, *, force_taf: bool = False) -> None:
        await self._fetch_product("metar", self.settings["metar_url"], select_metar)
        now = time.monotonic()
        taf_interval = float(self.settings["taf_refresh_interval_s"])
        if force_taf or now - self._last_taf_attempt >= taf_interval:
            self._last_taf_attempt = now
            await self._fetch_product("taf", self.settings["taf_url"], select_taf)

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        result = {"airport": self.airport, "provider": self.settings["provider"],
                  "status": "OK", "checked_at": utc_iso(now), "metar": dict(self._metar),
                  "taf": dict(self._taf), "message": None}
        for product in (result["metar"], result["taf"]):
            fetched = product.get("fetched_at")
            product["fetched_at_utc"] = utc_iso(fetched)
            product["age_s"] = None if fetched is None else max(0, now - float(fetched))
            product["observation_time_utc"] = utc_iso((product.get("report") or {}).get("obsTime"))
            product["issue_time_utc"] = utc_iso((product.get("report") or {}).get("issueTime"))
        metar = result["metar"]
        max_age = float(self.settings["max_metar_age_s"])
        if metar.get("report") and metar.get("observation_time_utc"):
            obs_ts = float(metar["report"].get("obsTime", 0) or 0)
            if now - obs_ts > max_age:
                metar["status"] = "STALE"
        statuses = {result["metar"]["status"], result["taf"]["status"]}
        if "DEGRADED" in statuses or "STALE" in statuses:
            result["status"] = "DEGRADED"
            result["message"] = "Weather data is degraded or stale; check the individual product status and timestamps."
        elif "CHECKING" in statuses:
            result["status"] = "CHECKING"
            result["message"] = "Waiting for the live weather provider."
        elif "NOT_AVAILABLE" in statuses:
            result["status"] = "NO_DATA" if "AVAILABLE" not in statuses else "DEGRADED"
            result["message"] = "A current report is not available from the live provider."
        return result

    async def run(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(float(self.settings["refresh_interval_s"]))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
