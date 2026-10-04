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
        self.speed = 10.0
        self.paused = False
        self._wake = asyncio.Event()
        self._wake.set()
        self._step_tokens = 0

    def set(self, timestamp: float) -> None:
        self._now = float(timestamp)

    def now(self) -> float:
        return self._now

    def isoformat(self) -> str:
        return datetime.fromtimestamp(self._now, timezone.utc).isoformat()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def wait_until(self, timestamp: float, speed: float) -> None:
        remaining_s = max(0.0, timestamp-self._now)/self.speed
        while True:
            if self.paused:
                if self._step_tokens > 0:
                    self._step_tokens -= 1
                    self.set(timestamp)
                    return
                self._wake.clear()
                await self._wake.wait()
                continue
            if remaining_s <= 0:
                self.set(timestamp)
                return
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=remaining_s)
                if not self.paused:
                    # User changed playback speed; restarting the remaining wait avoids stale speed.
                    remaining_s = max(0.0, timestamp-self._now)/self.speed
            except asyncio.TimeoutError:
                self.set(timestamp)
                return

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False
        self._wake.set()

    def step(self) -> None:
        self.paused = True
        self._step_tokens += 1
        self._wake.set()

    def set_speed(self, speed: float) -> None:
        self.speed = float(speed)
        self._wake.set()
