from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pyproj import Geod

from ..clock import LiveClock, ReplayClock
from ..collector import LiveCollector
from ..models import AirportCenter
from ..opensky import OpenSkySource
from ..replay import ReplaySource
from ..storage import SQLiteRecorder

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = Path(os.environ.get("AIR_COL_CONFIG", ROOT / "config.yaml"))
with CONFIG_PATH.open(encoding="utf-8") as stream:
    CONFIG = yaml.safe_load(stream)
AIRPORT = CONFIG["airport"]
CENTER = AirportCenter(AIRPORT["icao"], float(AIRPORT["latitude"]), float(AIRPORT["longitude"]))
_M_TO_FT = 3.280839895


class ReplayRequest(BaseModel):
    start_time: float | None = None
    end_time: float | None = None
    speed: float | None = Field(default=None, gt=0)


def make_collector(recorder, *, clock=None, mode="LIVE", source=None, on_publish=None) -> LiveCollector:
    if source is None:
        source = OpenSkySource(token_url=CONFIG["opensky"]["token_url"],
                               api_url=CONFIG["opensky"]["api_url"],
                               timeout_s=float(CONFIG["opensky"]["request_timeout_s"]))
    c = CONFIG["collector"]
    return LiveCollector(source=source, center=CENTER, radius_nm=float(AIRPORT["radius_nm"]),
                         poll_interval_s=float(c["poll_interval_s"]), max_backoff_s=float(c["max_backoff_s"]),
                         sparse_count_threshold=int(c["sparse_count_threshold"]),
                         low_altitude_ft=float(c["low_altitude_ft"]), manager_config=CONFIG["state_manager"],
                         recorder=recorder, daily_credit_quota=float(CONFIG["opensky"]["daily_credit_quota"]),
                         estimated_credits_per_request=float(CONFIG["opensky"]["estimated_credits_per_states_request"]),
                         clock=clock, mode=mode, record_live=(mode == "LIVE"), on_publish=on_publish)


def push_active(collector, message):
    if getattr(app.state, "active_collector", None) is not collector:
        return
    for queue in tuple(app.state.ws_queues):
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            pass


def activate(collector):
    app.state.active_collector = collector
    push_active(collector, collector.envelope("snapshot"))


async def finish_replay():
    task = getattr(app.state, "replay_task", None)
    source = getattr(app.state, "replay_source", None)
    collector = getattr(app.state, "replay_collector", None)
    if task is not None and task is not asyncio.current_task() and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    app.state.replay_task = None
    app.state.replay_source = None
    app.state.replay_collector = None
    activate(app.state.live_collector)
    return {"status": "stopped", "mode": "LIVE"}


async def run_replay(source: ReplaySource, collector: LiveCollector, clock: ReplayClock, speed: float):
    try:
        while not source.finished:
            timestamp = source.next_fetch_time
            if timestamp is None:
                break
            if clock.now() == 0:
                clock.set(timestamp)
            else:
                await clock.wait_until(timestamp, speed)
            await collector.poll_once()
    finally:
        if getattr(app.state, "active_collector", None) is collector:
            app.state.replay_task = None
            app.state.replay_source = None
            app.state.replay_collector = None
            activate(app.state.live_collector)


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = CONFIG["storage"]
    db_path = Path(storage["database_path"])
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    app.state.database_path = db_path
    app.state.ws_queues = set()
    app.state.replay_task = app.state.replay_source = app.state.replay_collector = None
    recorder = SQLiteRecorder(db_path, batch_size=int(storage["batch_size"]),
                              flush_interval_s=float(storage["flush_interval_s"]),
                              low_altitude_m=float(CONFIG["collector"]["low_altitude_ft"]) / _M_TO_FT,
                              low_quality_after_s=float(CONFIG["state_manager"]["low_quality_after_s"]))
    await recorder.start()
    app.state.recorder = recorder
    app.state.live_clock = LiveClock()
    app.state.live_collector = make_collector(recorder, clock=app.state.live_clock, on_publish=push_active)
    app.state.collector = app.state.live_collector
    app.state.active_collector = app.state.live_collector
    app.state.collector_task = asyncio.create_task(app.state.live_collector.run_forever())
    yield
    await finish_replay()
    app.state.collector_task.cancel()
    try:
        await app.state.collector_task
    except asyncio.CancelledError:
        pass
    await recorder.stop()
    await app.state.live_collector.source.close()


app = FastAPI(title="AIR_COL Live Research Dashboard", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CONFIG.get("api", {}).get("cors_origins", []),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
async def health():
    collector = app.state.active_collector
    return {"mode": collector.mode, "status": collector.status,
            "updated_at": collector.updated_at, "message": collector.message}


@app.get("/api/airport")
async def airport():
    geod = Geod(ellps="WGS84")
    step = int(CONFIG.get("web", {}).get("runway_ring_step_deg", 5))
    ring = [geod.fwd(CENTER.longitude, CENTER.latitude, bearing, float(AIRPORT["radius_nm"]) * 1852)[:2]
            for bearing in range(0, 360, step)]
    ring[-1] = ring[0]
    return {"icao": CENTER.icao, "latitude": CENTER.latitude, "longitude": CENTER.longitude,
            "radius_nm": float(AIRPORT["radius_nm"]), "radius_ring": ring,
            "map_zoom": int(CONFIG.get("web", {}).get("map_zoom", 9)),
            "altitude_bands_m": CONFIG.get("web", {}).get("altitude_bands_m", {}),
            "stale_after_s": float(CONFIG.get("web", {}).get("stale_after_s", 90)),
            "age_refresh_s": float(CONFIG.get("web", {}).get("age_refresh_s", 1)),
            "reconnect_initial_ms": int(CONFIG.get("web", {}).get("reconnect_initial_ms", 1000)),
            "reconnect_max_ms": int(CONFIG.get("web", {}).get("reconnect_max_ms", 15000)),
            "replay": CONFIG["replay"]}


@app.get("/api/aircraft")
async def aircraft():
    return app.state.active_collector.envelope("snapshot")


@app.get("/api/coverage")
async def coverage():
    settings = CONFIG["coverage"]
    since = app.state.live_clock.now() - float(settings["window_s"])
    return await asyncio.to_thread(app.state.recorder.coverage, since=since, bucket_s=int(settings["bucket_s"]))


@app.get("/api/replay/status")
async def replay_status():
    source = app.state.replay_source
    return {"mode": app.state.active_collector.mode, "running": source is not None,
            "completed_cycles": 0 if source is None else source._index,
            "total_cycles": 0 if source is None else source.total_cycles}


@app.post("/api/replay/start")
async def replay_start(request: ReplayRequest):
    settings = CONFIG["replay"]
    speed = float(settings["default_speed"] if request.speed is None else request.speed)
    if not float(settings["min_speed"]) <= speed <= float(settings["max_speed"]):
        raise HTTPException(422, f"speed must be between {settings['min_speed']} and {settings['max_speed']}x")
    if request.start_time is not None and request.end_time is not None and request.start_time > request.end_time:
        raise HTTPException(422, "start_time must be before end_time")
    if app.state.replay_task is not None:
        await finish_replay()
    source = ReplaySource(app.state.database_path, start_time=request.start_time, end_time=request.end_time)
    if source.total_cycles == 0:
        raise HTTPException(404, "No app-recorded live poll cycles in the requested replay range.")
    clock = ReplayClock()
    collector = make_collector(None, clock=clock, mode="REPLAY", source=source, on_publish=push_active)
    app.state.replay_source, app.state.replay_collector = source, collector
    app.state.replay_task = asyncio.create_task(run_replay(source, collector, clock, speed))
    activate(collector)
    return {"mode": "REPLAY", "status": "started", "speed": speed,
            "cycles": source.total_cycles, "start_time": source._times[0], "end_time": source._times[-1]}


@app.post("/api/replay/stop")
async def replay_stop():
    return await finish_replay()


@app.websocket("/ws/live")
async def live(websocket: WebSocket):
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=int(CONFIG["replay"].get("queue_size", 1)))
    app.state.ws_queues.add(queue)
    try:
        await websocket.send_json(app.state.active_collector.envelope("snapshot"))
        while True:
            await websocket.send_json(await queue.get())
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        app.state.ws_queues.discard(queue)
