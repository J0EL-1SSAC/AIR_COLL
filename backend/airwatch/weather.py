"""Live VOMM METAR/TAF retrieval and explicit availability reporting."""
from __future__ import annotations

import asyncio
import logging
import math
import re
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


def visibility_sm(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, str) and value.strip().endswith("+"):
            return float(value.strip()[:-1])
        return float(value)
    except (TypeError, ValueError):
        return None


def flight_category(visibility: Any, clouds: list[dict] | None) -> str | None:
    """Compute category using common ceiling/visibility thresholds (feet AGL, statute miles)."""
    vis = visibility_sm(visibility)
    ceilings = [float(layer["base"]) for layer in (clouds or [])
                if layer.get("cover") in {"BKN", "OVC", "VV"} and layer.get("base") is not None]
    ceiling = min(ceilings) if ceilings else None
    if (vis is not None and vis < 1) or (ceiling is not None and ceiling < 500):
        return "LIFR"
    if (vis is not None and vis < 3) or (ceiling is not None and ceiling < 1000):
        return "IFR"
    if (vis is not None and vis <= 5) or (ceiling is not None and ceiling <= 3000):
        return "MVFR"
    if vis is not None or ceiling is not None:
        return "VFR"
    return None


def decode_metar(record: dict | None) -> dict[str, Any] | None:
    if not record:
        return None
    raw = str(record.get("rawOb") or "")
    variable = bool(re.search(r"\bVRB\d{2,3}KT\b", raw))
    variable_range = re.search(r"\b(\d{3})V(\d{3})\b", raw)
    direction = record.get("wdir")
    if variable or variable_range or str(direction).upper() == "VRB":
        direction = None
    clouds = record.get("clouds") if isinstance(record.get("clouds"), list) else []
    altim = record.get("altim")
    try:
        qnh_hpa = float(altim) * 33.8638866667 if float(altim) < 50 else float(altim)
    except (TypeError, ValueError):
        qnh_hpa = None
    return {"station": record.get("icaoId"), "raw_metar": raw,
            "observation_time_utc": utc_iso(record.get("obsTime")),
            "wind_direction_true_deg": direction, "wind_direction_variable": bool(variable or variable_range),
            "wind_direction_range_deg": ([int(variable_range.group(1)), int(variable_range.group(2))]
                                          if variable_range else None),
            "wind_speed_kt": record.get("wspd"), "wind_gust_kt": record.get("wgst"),
            "visibility_sm": record.get("visib"), "present_weather": record.get("wxString"),
            "cloud_layers": clouds, "temperature_c": record.get("temp"),
            "dewpoint_c": record.get("dewp"), "qnh_hpa": qnh_hpa,
            "flight_category": flight_category(record.get("visib"), clouds),
            "flight_category_rule": "Ceiling/visibility: LIFR <500 ft or <1 SM; IFR <1,000 ft or <3 SM; MVFR <=3,000 ft or <=5 SM; otherwise VFR. Ceiling is lowest BKN/OVC/VV base.",
            "wind_direction_assumption": "METAR wind direction is treated as degrees true for runway comparison."}


def runway_wind_components(direction_deg: Any, speed_kt: Any, gust_kt: Any,
                           runway_ends: list[dict]) -> list[dict]:
    """Positive headwind is toward the nose; signed crosswind is right-positive."""
    try:
        direction, speed = float(direction_deg), float(speed_kt)
    except (TypeError, ValueError):
        return [{"runway_end": end["identifier"], "headwind_kt": None, "crosswind_kt": None,
                 "reason": "Wind direction/speed unavailable or direction variable."} for end in runway_ends]
    gust = None
    try:
        if gust_kt is not None:
            gust = float(gust_kt)
    except (TypeError, ValueError):
        pass
    results=[]
    for end in runway_ends:
        difference=math.radians(direction-float(end["true_heading_deg"]))
        results.append({"runway_end":end["identifier"],
            "headwind_kt":speed*math.cos(difference), "crosswind_kt":speed*math.sin(difference),
            "gust_headwind_kt":None if gust is None else gust*math.cos(difference),
            "gust_crosswind_kt":None if gust is None else gust*math.sin(difference), "reason":None})
    return results


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
        self._last_http: dict[str, Any] = {"metar": None, "taf": None}
        self._last_taf_attempt = 0.0

    async def _fetch_product(self, key: str, url: str, selector) -> None:
        now = time.time()
        previous = getattr(self, f"_{key}")
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=float(self.settings["request_timeout_s"]),
                    headers={"User-Agent": self.settings["user_agent"], "Accept": "application/json"})
            response = await self._client.get(url, params={"ids": self.airport, "format": "json"})
            self._last_http[key] = {"status_code": response.status_code, "url": str(response.url)}
            if response.status_code == 204:
                setattr(self, f"_{key}", {"status": "NOT_AVAILABLE", "report": None, "error": None,
                                           "fetched_at": now})
                return
            response.raise_for_status()
            payload = response.json()
            self._last_http[key]["json"] = payload
            record = selector(payload)
            setattr(self, f"_{key}", {"status": "AVAILABLE" if record else "NOT_AVAILABLE",
                                       "report": record, "error": None, "fetched_at": now})
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            self._last_http[key] = {"url": f"{url}?ids={self.airport}&format=json", "status_code": None,
                                    "error": str(exc)}
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
        result["provider_diagnostics"] = dict(self._last_http)
        for product in (result["metar"], result["taf"]):
            fetched = product.get("fetched_at")
            product["fetched_at_utc"] = utc_iso(fetched)
            product["age_s"] = None if fetched is None else max(0, now - float(fetched))
            product["observation_time_utc"] = utc_iso((product.get("report") or {}).get("obsTime"))
            product["issue_time_utc"] = utc_iso((product.get("report") or {}).get("issueTime"))
            product["age_min"] = None if product["age_s"] is None else product["age_s"] / 60.0
        metar = result["metar"]
        metar["decoded"] = decode_metar(metar.get("report"))
        if metar["decoded"]:
            obs_time = (metar.get("report") or {}).get("obsTime")
            metar["decoded"]["age_min"] = None if obs_time is None else max(0.0, (now-float(obs_time))/60.0)
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
