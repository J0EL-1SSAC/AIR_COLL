from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from pyproj import Transformer

from .models import AirportCenter
from .opensky import DataSource
from .state_manager import AircraftStateManager
from .storage import SQLiteRecorder

logger = logging.getLogger(__name__)
_M_TO_FT = 3.280839895


class LiveCollector:
    """Reusable live poller shared by the API and CLI; it never manufactures data."""
    def __init__(self, *, source: DataSource, center: AirportCenter, radius_nm: float,
                 poll_interval_s: float, max_backoff_s: float, sparse_count_threshold: int,
                 low_altitude_ft: float, manager_config: dict, recorder: SQLiteRecorder):
        self.source, self.center, self.radius_nm = source, center, radius_nm
        self.poll_interval_s, self.max_backoff_s = poll_interval_s, max_backoff_s
        self.sparse_count_threshold, self.low_altitude_m = sparse_count_threshold, low_altitude_ft / _M_TO_FT
        self.latest: list[dict[str, Any]] = []
        self.status = "NO_DATA"
        self.message = "Waiting for the first live OpenSky response."
        self.updated_at: str | None = None
        self.aircraft_count = 0
        self.low_or_ground_count = 0
        self._listeners: set[asyncio.Queue] = set()
        self._transform = Transformer.from_crs("EPSG:4326", f"+proj=aeqd +lat_0={center.latitude} +lon_0={center.longitude} +datum=WGS84 +units=m +no_defs", always_xy=True)
        self.recorder = recorder
        self.manager = AircraftStateManager(latitude=center.latitude, longitude=center.longitude,
            history_length=int(manager_config["history_length"]),
            low_quality_after_s=float(manager_config["low_quality_after_s"]),
            drop_after_s=float(manager_config["drop_after_s"]), tombstone_s=float(manager_config["tombstone_s"]),
            low_altitude_ft=low_altitude_ft, smoothing_enabled=bool(manager_config["smoothing_enabled"]),
            alpha=float(manager_config["alpha"]), beta=float(manager_config["beta"]), transform=self._transform)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._listeners.discard(queue)

    def _publish(self, kind: str) -> None:
        envelope = self.envelope(kind)
        for queue in tuple(self._listeners):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                pass

    def envelope(self, kind: str) -> dict[str, Any]:
        return {"type": kind, "ts": self.updated_at or datetime.now(timezone.utc).isoformat(),
                "source_status": self.status,
                "data": {"aircraft": self.latest, "message": self.message,
                         "aircraft_count": self.aircraft_count,
                         "low_or_ground_count": self.low_or_ground_count,
                         "recently_lost": [item["state"] for item in self.manager.recently_lost.values()]}}

    async def poll_once(self) -> bool:
        self.updated_at = datetime.now(timezone.utc).isoformat()
        try:
            states = await self.source.fetch_states(self.center, self.radius_nm)
        except Exception as exc:
            logger.warning("OpenSky degraded: %s", exc)
            now = datetime.now(timezone.utc).timestamp()
            self.manager.expire(now)
            self.latest = self.manager.snapshot(now)
            counts = self.manager.coverage_counts(now)
            self.aircraft_count = counts["aircraft_count"]
            self.low_or_ground_count = counts["low_or_ground_count"]
            self.status, self.message = "DEGRADED", f"Live source unavailable: {exc}"
            self._publish("status")
            return False
        now = datetime.now(timezone.utc).timestamp()
        await self.recorder.record_batch(states, now, "opensky")
        self.manager.update(states, now)
        self.latest = self.manager.snapshot(now)
        counts = self.manager.coverage_counts(now)
        self.aircraft_count = counts["aircraft_count"]
        self.low_or_ground_count = counts["low_or_ground_count"]
        if not self.latest:
            self.status, self.message = "NO_DATA", "OpenSky returned no positioned aircraft in the configured radius."
            kind = "status"
        elif len(self.latest) < self.sparse_count_threshold:
            self.status, self.message = "DEGRADED", f"Sparse public ADS-B coverage: {len(self.latest)} aircraft observed."
            kind = "snapshot"
        else:
            self.status, self.message, kind = "OK", "Live OpenSky data received.", "snapshot"
        self._publish(kind)
        return True

    async def run_forever(self) -> None:
        failures = 0
        while True:
            success = await self.poll_once()
            if success:
                failures = 0
                delay = self.poll_interval_s
            else:
                failures += 1
                delay = min(self.max_backoff_s, self.poll_interval_s * (2 ** min(failures - 1, 8)))
            await asyncio.sleep(delay)
