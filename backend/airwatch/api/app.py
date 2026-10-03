from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pyproj import Geod

from ..clock import LiveClock, ReplayClock
from ..alerts import AlertManager
from ..collector import LiveCollector
from ..event_store import SQLiteEventStore
from ..models import AirportCenter
from ..opensky import OpenSkySource
from ..pairs import compute_pair_cpas
from ..replay import ReplaySource
from ..risk import validate_timing_settings
from ..storage import SQLiteRecorder
from ..runways import RunwayDataError, load_runways

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = Path(os.environ.get("AIR_COL_CONFIG", ROOT / "config.yaml"))
with CONFIG_PATH.open(encoding="utf-8") as stream:
    CONFIG = yaml.safe_load(stream)
AIRPORT = CONFIG["airport"]
CENTER = AirportCenter(AIRPORT["icao"], float(AIRPORT["latitude"]), float(AIRPORT["longitude"]))
_M_TO_FT = 3.280839895
logger = logging.getLogger(__name__)


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
    risk_settings = CONFIG["risk"]
    profile = os.environ.get("AIR_COL_RISK_PROFILE", risk_settings["active_profile"])
    if profile not in risk_settings["profiles"]:
        raise ValueError(f"Unknown AIR_COL_RISK_PROFILE: {profile}")
    restored = app.state.event_store.recent(mode=mode, risk_profile=profile)
    alert_manager = AlertManager(settings=risk_settings, airport=CENTER.icao, mode=mode,
                                 risk_profile=profile, restored_events=restored)
    collector = LiveCollector(source=source, center=CENTER, radius_nm=float(AIRPORT["radius_nm"]),
                         poll_interval_s=float(c["poll_interval_s"]), max_backoff_s=float(c["max_backoff_s"]),
                         sparse_count_threshold=int(c["sparse_count_threshold"]),
                         low_altitude_ft=float(c["low_altitude_ft"]), manager_config=CONFIG["state_manager"],
                         recorder=recorder, daily_credit_quota=float(CONFIG["opensky"]["daily_credit_quota"]),
                         estimated_credits_per_request=float(CONFIG["opensky"]["estimated_credits_per_states_request"]),
                         clock=clock, mode=mode, record_live=(mode == "LIVE"), on_publish=on_publish,
                         prediction_config=CONFIG["prediction"], alert_processor=process_alert_cycle)
    collector.alert_manager = alert_manager
    return collector


async def process_alert_cycle(collector: LiveCollector) -> None:
    now = collector.clock.now()
    states = collector.manager.snapshot(now, include_unpositioned=True)
    result = compute_pair_cpas(
        states, airport_radius_nm=float(AIRPORT["radius_nm"]), cpa_settings=CONFIG["cpa"],
        prediction_settings=CONFIG["prediction"], inverse_transformer=collector.predictor.inverse_transformer)
    changed = collector.alert_manager.process_cycle(result["pairs"], states, now)
    await app.state.event_store.upsert_many([item.get("event", item) for item in changed])
    for item in changed:
        sub_event = item.get("sub_event")
        if sub_event in {"opened", "updated", "escalated", "resolved"}:
            push_active(collector, {"type": "alert", "mode": collector.mode,
                                    "ts": collector.clock.isoformat(), "source_status": collector.status,
                                    "data": {"sub_event": sub_event, "event": item["event"]}})


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
            reason = "replay_ended" if source.finished else "replay_stopped"
            changes = collector.alert_manager.finalize(clock.now(), reason)
            await app.state.event_store.upsert_many([item["event"] for item in changes])
            for item in changes:
                push_active(collector, {"type": "alert", "mode": collector.mode,
                                        "ts": clock.isoformat(), "source_status": collector.status,
                                        "data": {"sub_event": "resolved", "event": item["event"]}})
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
    app.state.event_store = SQLiteEventStore(db_path)
    await asyncio.to_thread(app.state.event_store.initialize)
    for warning in validate_timing_settings(CONFIG):
        logger.warning("Alert timing configuration: %s", warning)
    logger.info("Potential Conflict risk profile: %s", os.environ.get("AIR_COL_RISK_PROFILE", CONFIG["risk"]["active_profile"]))
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
            "updated_at": collector.updated_at, "message": collector.message,
            "risk_profile": collector.alert_manager.risk_profile}


@app.get("/api/risk/config")
async def risk_config():
    settings = CONFIG["risk"]
    profile = app.state.active_collector.alert_manager.risk_profile
    return {"active_profile": profile, "min_alert_level": settings["min_alert_level"],
            "allow_low_watch": settings["allow_low_watch"], "lookahead_s": settings["lookahead_s"],
            "max_data_age_for_alert_s": settings["max_data_age_for_alert_s"],
            "confirmation": settings["confirmation"], "clear": settings["clear"],
            "cooldown_s": settings["cooldown_s"], "data_lost_timeout_s": settings["data_lost_timeout_s"],
            "treat_unknown_vertical_as": settings["treat_unknown_vertical_as"],
            "filters": settings["filters"], "profile": settings["profiles"][profile]}


@app.get("/api/alerts/active")
async def active_alerts(mode: str | None = None):
    selected_mode = mode or app.state.active_collector.mode
    if selected_mode not in {"LIVE", "REPLAY"}:
        raise HTTPException(422, "mode must be LIVE or REPLAY")
    alerts = await asyncio.to_thread(app.state.event_store.active, mode=selected_mode)
    return {"mode": selected_mode, "alerts": alerts}


@app.get("/api/events")
async def events(status: str | None = None, risk: str | None = None, mode: str = "LIVE",
                 aircraft: str | None = None, start: float | None = None, end: float | None = None,
                 limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)):
    if mode not in {"LIVE", "REPLAY"}:
        raise HTTPException(422, "mode must be LIVE or REPLAY")
    if start is not None and end is not None and start > end:
        raise HTTPException(422, "start must be earlier than end")
    if risk and risk.upper() not in {"LOW", "MEDIUM", "HIGH", "CRITICAL", "NORMAL"}:
        raise HTTPException(422, "risk must be LOW, MEDIUM, HIGH, CRITICAL, or NORMAL")
    result = await asyncio.to_thread(app.state.event_store.query, status=status, risk=risk, mode=mode,
                                     aircraft=aircraft, start_ts=start, end_ts=end,
                                     limit=limit, offset=offset)
    return {"mode": mode, "events": result, "limit": limit, "offset": offset}


@app.get("/api/events/{event_id}")
async def event_detail(event_id: str):
    event = await asyncio.to_thread(app.state.event_store.get, event_id)
    if event is None:
        raise HTTPException(404, "Event not found.")
    return event


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
            "low_altitude_ft": float(CONFIG["collector"]["low_altitude_ft"]),
            "labels_min_zoom": int(CONFIG["web"].get("labels_min_zoom", 11)),
            "prediction_ticks_min_zoom": int(CONFIG["web"].get("prediction_ticks_min_zoom", 11)),
            "label_exclusion_nm": float(CONFIG["web"].get("label_exclusion_nm", 2)),
            "trail_oldest_opacity": float(CONFIG["web"].get("trail_oldest_opacity", 0.18)),
            "trail_newest_opacity": float(CONFIG["web"].get("trail_newest_opacity", 0.82)),
            "replay": CONFIG["replay"], "cpa_display": {
                "near_nm": float(CONFIG["cpa"]["display_near_nm"]),
                "amber_nm": float(CONFIG["cpa"]["display_amber_nm"]),
            }}


@app.get("/api/aircraft")
async def aircraft():
    return app.state.active_collector.envelope("snapshot")


def current_predictions(collector):
    now = collector.clock.now()
    collector.manager.expire(now)
    states = collector.manager.snapshot(now, include_unpositioned=True)
    settings = CONFIG["prediction"]
    horizons = settings["horizons_s"]
    predictions = [collector.predictor.predict(state, horizons) for state in states]
    return {"mode": collector.mode, "updated_at": collector.clock.isoformat(),
            "model": settings["model"], "horizons_s": horizons, "predictions": predictions}


@app.get("/api/predictions")
async def predictions():
    return current_predictions(app.state.active_collector)


@app.get("/api/aircraft/{icao24}/prediction")
async def aircraft_prediction(icao24: str):
    collector = app.state.active_collector
    result = current_predictions(collector)
    prediction = next((item for item in result["predictions"] if item["icao24"].lower() == icao24.lower()), None)
    if prediction is None:
        raise HTTPException(404, "Aircraft is not currently active in the selected pipeline.")
    return {"mode": result["mode"], "updated_at": result["updated_at"], "model": result["model"],
            "horizons_s": result["horizons_s"], "prediction": prediction}


@app.get("/api/pairs")
async def pairs():
    collector = app.state.active_collector
    now = collector.clock.now()
    collector.manager.expire(now)
    states = collector.manager.snapshot(now, include_unpositioned=True)
    result = compute_pair_cpas(
        states,
        airport_radius_nm=float(AIRPORT["radius_nm"]),
        cpa_settings=CONFIG["cpa"],
        prediction_settings=CONFIG["prediction"],
        inverse_transformer=collector.predictor.inverse_transformer,
    )
    return {"mode": collector.mode, "updated_at": collector.clock.isoformat(), **result}


@app.get("/api/coverage")
async def coverage():
    settings = CONFIG["coverage"]
    since = app.state.live_clock.now() - float(settings["window_s"])
    return await asyncio.to_thread(app.state.recorder.coverage, since=since, bucket_s=int(settings["bucket_s"]))


def runway_definitions():
    settings = dict(CONFIG["runways"])
    data_path = Path(settings["data_file"])
    override_path = Path(settings["override_file"])
    settings["data_file"] = data_path if data_path.is_absolute() else ROOT / data_path
    settings["override_file"] = override_path if override_path.is_absolute() else ROOT / override_path
    return load_runways(airport_ident=CENTER.icao, latitude=float(AIRPORT["latitude"]),
                        longitude=float(AIRPORT["longitude"]), settings=settings)


@app.get("/api/runways")
async def runways():
    try:
        definitions = await asyncio.to_thread(runway_definitions)
    except RunwayDataError as error:
        raise HTTPException(503, str(error)) from error
    from ..geometry import local_transformers
    _forward, inverse = local_transformers(float(AIRPORT["latitude"]), float(AIRPORT["longitude"]))
    return {"airport": CENTER.icao, "runways": [item.as_dict(inverse_transformer=inverse) for item in definitions]}


@app.get("/api/coverage/runway-summary")
async def runway_coverage_summary():
    output = CONFIG["coverage_report"].get("output_dir", "reports/coverage")
    path = Path(output)
    if not path.is_absolute():
        path = ROOT / path
    summary_path = path / "coverage_summary.json"
    if not summary_path.exists():
        raise HTTPException(404, "No coverage report is available yet. Run scripts/coverage_report.py to generate it.")
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(503, "The saved coverage summary cannot be read.") from error
    return {"recording": data.get("recording"), "runways_available": data.get("runways_available"),
            "low_altitude_reports_per_hour": data.get("low_altitude_reports_per_hour"),
            "on_ground_reports_per_hour": data.get("on_ground_reports_per_hour"),
            "runway_buffer_report_total": data.get("runway_buffer_report_total"),
            "recommendations": data.get("recommendations")}


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
