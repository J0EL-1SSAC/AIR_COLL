from __future__ import annotations

import logging
import json
import math
import os
import time
from typing import Protocol

import httpx
from pyproj import Geod

from .models import AircraftState, AirportCenter

logger = logging.getLogger(__name__)
_NM_TO_M = 1852.0


class DataSource(Protocol):
    async def fetch_states(self, center: AirportCenter, radius_nm: float) -> list[AircraftState]: ...


class OpenSkyError(RuntimeError):
    """An OpenSky request failed; callers must present this as degraded data."""


class OpenSkyRateLimitError(OpenSkyError):
    def __init__(self, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


def retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


class OpenSkySource:
    def __init__(self, *, token_url: str, api_url: str, timeout_s: float):
        self.token_url = token_url
        self.api_url = api_url
        self.timeout_s = timeout_s
        self.client_id = os.getenv("OPENSKY_CLIENT_ID")
        self.client_secret = os.getenv("OPENSKY_CLIENT_SECRET")
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._http = httpx.AsyncClient(timeout=timeout_s)

    async def close(self) -> None:
        await self._http.aclose()

    def _bbox(self, center: AirportCenter, radius_nm: float) -> dict[str, float]:
        # Bounding box encloses the configured radius; final radial filtering is local/geodesic.
        geod = Geod(ellps="WGS84")
        radius_m = radius_nm * _NM_TO_M
        west = geod.fwd(center.longitude, center.latitude, 270, radius_m)[0]
        south = geod.fwd(center.longitude, center.latitude, 180, radius_m)[1]
        east = geod.fwd(center.longitude, center.latitude, 90, radius_m)[0]
        north = geod.fwd(center.longitude, center.latitude, 0, radius_m)[1]
        return {"lamin": south, "lamax": north, "lomin": west, "lomax": east}

    async def _access_token(self) -> str:
        if not self.client_id or not self.client_secret:
            raise OpenSkyError("OpenSky credentials missing; set OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET")
        if self._token and time.time() < self._token_expires_at - 30:
            return self._token
        try:
            response = await self._http.post(
                self.token_url,
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
            )
            response.raise_for_status()
            payload = response.json()
            self._token = payload["access_token"]
            self._token_expires_at = time.time() + float(payload.get("expires_in", 300))
            return self._token
        except httpx.HTTPStatusError as exc:
            response = exc.response
            if response.status_code == 429:
                retry_value = response.headers.get("X-Rate-Limit-Retry-After-Seconds") or response.headers.get("Retry-After")
                retry_after = retry_after_seconds(retry_value)
                logger.warning("OpenSky OAuth rate limited; X-Rate-Limit-Remaining=%s; retry after=%s",
                               response.headers.get("X-Rate-Limit-Remaining", "not provided"),
                               retry_value if retry_value is not None else "not provided")
                raise OpenSkyRateLimitError("OpenSky OAuth rate limited (HTTP 429)", retry_after) from exc
            raise OpenSkyError(f"OpenSky OAuth token request failed: {exc}") from exc
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise OpenSkyError(f"OpenSky OAuth token request failed: {exc}") from exc

    async def fetch_states(self, center: AirportCenter, radius_nm: float) -> list[AircraftState]:
        token = await self._access_token()
        params = self._bbox(center, radius_nm)
        try:
            response = await self._http.get(
                self.api_url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            remaining = response.headers.get("X-Rate-Limit-Remaining")
            reset = response.headers.get("X-Rate-Limit-Reset")
            retry_header = response.headers.get("X-Rate-Limit-Retry-After-Seconds")
            logger.info("OpenSky states request: HTTP %s; X-Rate-Limit-Remaining=%s; X-Rate-Limit-Reset=%s; X-Rate-Limit-Retry-After-Seconds=%s",
                        response.status_code, remaining if remaining is not None else "not provided",
                        reset if reset is not None else "not provided",
                        retry_header if retry_header is not None else "not provided")
            if response.status_code == 429:
                retry_value = retry_header or response.headers.get("Retry-After")
                retry_after = retry_after_seconds(retry_value)
                raise OpenSkyRateLimitError("OpenSky rate limited (HTTP 429)", retry_after)
            if response.status_code >= 500:
                raise OpenSkyError(f"OpenSky temporarily unavailable (HTTP {response.status_code})")
            response.raise_for_status()
            payload = response.json()
        except OpenSkyError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise OpenSkyError(f"OpenSky states request failed: {exc}") from exc

        states: list[AircraftState] = []
        now = time.time()
        for row in payload.get("states") or []:
            if len(row) < 17:
                continue
            # OpenSky fields: 0 icao24, 1 callsign, 2 origin country, 3 time_position,
            # 4 last_contact, 5 lon, 6 lat, 7 baro altitude, 8 on_ground, 9 velocity,
            # 10 true track, 11 vertical rate, 13 geo altitude.
            lon = None if row[5] is None else float(row[5])
            lat = None if row[6] is None else float(row[6])
            if lon is not None and lat is not None:
                _, _, distance_m = Geod(ellps="WGS84").inv(center.longitude, center.latitude, lon, lat)
                if distance_m > radius_nm * _NM_TO_M:
                    continue
            states.append(AircraftState(
                icao24=str(row[0]), callsign=(str(row[1]).strip() or None) if row[1] else None,
                latitude=lat, longitude=lon,
                baro_altitude_m=row[7], geo_altitude_m=row[13], velocity_mps=row[9],
                track_deg=row[10], vertical_rate_mps=row[11], on_ground=None if row[8] is None else bool(row[8]),
                position_timestamp=row[3], last_contact=row[4], raw_payload_json=json.dumps(row, separators=(",", ":")),
            ))
        return states
