from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pyproj import Geod

from ..collector import LiveCollector
from ..models import AirportCenter
from ..opensky import OpenSkySource
from ..storage import SQLiteRecorder

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = Path(os.environ.get("AIR_COL_CONFIG", ROOT / "config.yaml"))
with CONFIG_PATH.open(encoding="utf-8") as stream:
    CONFIG = yaml.safe_load(stream)
AIRPORT = CONFIG["airport"]
CENTER = AirportCenter(AIRPORT["icao"], float(AIRPORT["latitude"]), float(AIRPORT["longitude"]))
_M_TO_FT = 3.280839895


def make_collector(recorder: SQLiteRecorder) -> LiveCollector:
    source = OpenSkySource(token_url=CONFIG["opensky"]["token_url"],
                           api_url=CONFIG["opensky"]["api_url"],
                           timeout_s=float(CONFIG["opensky"]["request_timeout_s"]))
    c = CONFIG["collector"]
    return LiveCollector(source=source, center=CENTER, radius_nm=float(AIRPORT["radius_nm"]),
                         poll_interval_s=float(c["poll_interval_s"]),
                         max_backoff_s=float(c["max_backoff_s"]),
                         sparse_count_threshold=int(c["sparse_count_threshold"]),
                         low_altitude_ft=float(c["low_altitude_ft"]),
                         manager_config=CONFIG["state_manager"], recorder=recorder)


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = CONFIG["storage"]
    db_path = Path(storage["database_path"])
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    recorder = SQLiteRecorder(db_path, batch_size=int(storage["batch_size"]),
                              flush_interval_s=float(storage["flush_interval_s"]),
                              low_altitude_m=float(CONFIG["collector"]["low_altitude_ft"]) / _M_TO_FT,
                              low_quality_after_s=float(CONFIG["state_manager"]["low_quality_after_s"]))
    await recorder.start()
    app.state.recorder = recorder
    app.state.collector = make_collector(recorder)
    app.state.collector_task = asyncio.create_task(app.state.collector.run_forever())
    yield
    app.state.collector_task.cancel()
    try:
        await app.state.collector_task
    except asyncio.CancelledError:
        pass
    await recorder.stop()
    await app.state.collector.source.close()


app = FastAPI(title="AIR_COL Live Research Dashboard", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CONFIG.get("api", {}).get("cors_origins", []),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
async def health():
    collector = app.state.collector
    return {"status": collector.status, "updated_at": collector.updated_at, "message": collector.message}


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
            "reconnect_max_ms": int(CONFIG.get("web", {}).get("reconnect_max_ms", 15000))}


@app.get("/api/aircraft")
async def aircraft():
    collector = app.state.collector
    return collector.envelope("snapshot")


@app.get("/api/coverage")
async def coverage():
    settings = CONFIG["coverage"]
    since = time.time() - float(settings["window_s"])
    return await asyncio.to_thread(app.state.recorder.coverage,
                                   since=since, bucket_s=int(settings["bucket_s"]))


@app.websocket("/ws/live")
async def live(websocket: WebSocket):
    await websocket.accept()
    collector: LiveCollector = app.state.collector
    queue = collector.subscribe()
    try:
        await websocket.send_json(collector.envelope("snapshot"))
        while True:
            await websocket.send_json(await queue.get())
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        collector.unsubscribe(queue)
