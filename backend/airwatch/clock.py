"""Wall and virtual clocks used by live and replay pipelines."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...
    def isoformat(self) -> str: ...
    async def sleep(self, seconds: float) -> None: ...


class LiveClock:
    def now(self) -> float:
        return time.time()

    def isoformat(self) -> str:
        return datetime.fromtimestamp(self.now(), timezone.utc).isoformat()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class ReplayClock:
    """Virtual UTC clock; waits at requested playback speed, then lands on source time."""
    def __init__(self):
        self._now = 0.0

    def set(self, timestamp: float) -> None:
        self._now = float(timestamp)

    def now(self) -> float:
        return self._now

    def isoformat(self) -> str:
        return datetime.fromtimestamp(self._now, timezone.utc).isoformat()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def wait_until(self, timestamp: float, speed: float) -> None:
        delay = max(0.0, timestamp - self._now) / speed
        await asyncio.sleep(delay)
        self.set(timestamp)
