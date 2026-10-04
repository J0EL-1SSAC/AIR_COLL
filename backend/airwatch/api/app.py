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
from ..approach import ApproachTracker
from ..occupancy import OccupancyTracker
from ..approach_store import SQLiteApproachStore
from ..frequencies import load_frequencies, estimate_facility_roles, estimate_aircraft_facilities
from ..airport_reference import AirportReferenceIndex
from ..enrichment import EnrichmentService
from ..activity_store import AirportActivityStore
from ..airport_activity import infer_landing, infer_departure
from ..weather import LiveWeatherService
from ..replay_summary_store import ReplaySummaryStore

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
    collector.approach_tracker = ApproachTracker(CONFIG.get("approach", {}))
    collector.occupancy_tracker = OccupancyTracker(CONFIG.get("occupancy", {}))
    collector.runway_definitions = []
    collector.current_approaches = []
    collector.current_occupancy = {"runways": {}, "experimental": True}
    try:
        collector.runway_definitions = runway_definitions()
    except RunwayDataError as error:
        logger.warning("Runway surveillance unavailable: %s", error)
    return collector


async def process_alert_cycle(collector: LiveCollector) -> None:
    now = collector.clock.now()
    states = collector.manager.snapshot(now, include_unpositioned=True)
    approaches = collector.approach_tracker.update(states, collector.runway_definitions, now)
    collector.current_approaches = approaches
    for approach in approaches:
        await asyncio.to_thread(app.state.approach_store.upsert, collector.mode, approach)
        inferred = infer_landing(approach, CONFIG["airport_activity"],
            airport_elevation_m=float(CONFIG["coverage_report"]["ground_elevation_m"]))
        if inferred:
            await asyncio.to_thread(app.state.activity_store.upsert, collector.mode, inferred)
    runway_ends=[end for runway in collector.runway_definitions for end in (runway.end_a,runway.end_b)]
    for state in states:
        inferred = infer_departure(state, runway_ends, CONFIG["airport_activity"],
            airport_elevation_m=float(CONFIG["coverage_report"]["ground_elevation_m"]))
        if inferred:
            await asyncio.to_thread(app.state.activity_store.upsert, collector.mode, inferred)
    gate = occupancy_coverage_gate()
    collector.current_occupancy = collector.occupancy_tracker.update(
        states, collector.runway_definitions, now, gate["assessable"], gate)
    result = compute_pair_cpas(
        states, airport_radius_nm=float(AIRPORT["radius_nm"]), cpa_settings=CONFIG["cpa"],
        prediction_settings=CONFIG["prediction"], inverse_transformer=collector.predictor.inverse_transformer)
    changed = collector.alert_manager.process_cycle(result["pairs"], states, now)
    collector.last_pair_count = len(result["pairs"])
    collector.last_alerts_opened = sum(1 for item in changed if item.get("sub_event") == "opened")
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
    session_id = f"{source._times[0]:.3f}"
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
            states = collector.latest
            low_altitude_count = sum(
                1 for state in states
                if state.get("on_ground") is True or (state.get("baro_altitude_m") is not None
                    and state["baro_altitude_m"] < collector.low_altitude_m)
            )
            on_ground_count = sum(1 for state in states if state.get("on_ground") is True)
            await asyncio.to_thread(app.state.replay_summary_store.record, session_id, timestamp,
                collector.aircraft_count, low_altitude_count, on_ground_count,
                getattr(collector, "last_pair_count", 0), getattr(collector, "last_alerts_opened", 0), clock.now())
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
    app.state.approach_store = SQLiteApproachStore(db_path)
    await asyncio.to_thread(app.state.approach_store.initialize)
    app.state.activity_store = AirportActivityStore(db_path)
    await asyncio.to_thread(app.state.activity_store.initialize)
    app.state.replay_summary_store = ReplaySummaryStore(db_path)
    await asyncio.to_thread(app.state.replay_summary_store.initialize)
    airport_reference_settings = CONFIG["airport_reference"]
    app.state.enrichment_service = EnrichmentService(settings=CONFIG["enrichment"], db_path=db_path,
        airport_index=AirportReferenceIndex(ROOT / airport_reference_settings["data_file"],
                                           ROOT / airport_reference_settings["database_file"]))
    for warning in validate_timing_settings(CONFIG):
        logger.warning("Alert timing configuration: %s", warning)
    logger.info("Potential Conflict risk profile: %s", os.environ.get("AIR_COL_RISK_PROFILE", CONFIG["risk"]["active_profile"]))
    if float(CONFIG.get("approach", {}).get("max_data_age_s", 0)) <= float(CONFIG["collector"]["poll_interval_s"]):
        logger.warning("Approach max_data_age_s should exceed poll_interval_s for useful sampled approach tracking.")
    if float(CONFIG.get("occupancy", {}).get("max_data_age_s", 0)) <= float(CONFIG["collector"]["poll_interval_s"]):
        logger.warning("Occupancy max_data_age_s should exceed poll_interval_s for useful experimental occupancy tracking.")
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
    app.state.weather_service = LiveWeatherService(airport=CENTER.icao, settings=CONFIG["weather"])
    app.state.weather_task = asyncio.create_task(app.state.weather_service.run())
    yield
    app.state.weather_task.cancel()
    try:
        await app.state.weather_task
    except asyncio.CancelledError:
        pass
    await app.state.weather_service.close()
    await app.state.enrichment_service.close()
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
async def events(status: str | None = None, risk: str | None = None, mode: str | None = None,
                 aircraft: str | None = None, start: float | None = None, end: float | None = None,
                 limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)):
    selected_mode = (mode or getattr(getattr(app.state, "active_collector", None), "mode", "LIVE")).upper()
    if selected_mode not in {"LIVE", "REPLAY", "ALL"}:
        raise HTTPException(422, "mode must be LIVE, REPLAY, or ALL")
    if start is not None and end is not None and start > end:
        raise HTTPException(422, "start must be earlier than end")
    if risk and risk.upper() not in {"LOW", "MEDIUM", "HIGH", "CRITICAL", "NORMAL"}:
        raise HTTPException(422, "risk must be LOW, MEDIUM, HIGH, CRITICAL, or NORMAL")
    result = await asyncio.to_thread(app.state.event_store.query, status=status, risk=risk,
                                     mode=None if selected_mode == "ALL" else selected_mode,
                                     aircraft=aircraft, start_ts=start, end_ts=end,
                                     limit=limit, offset=offset)
    counts = await asyncio.to_thread(app.state.event_store.counts_by_mode)
    message = None
    if not result:
        if selected_mode == "ALL":
            message = f"0 events match these filters. Database totals: {counts.get('LIVE', 0)} LIVE, {counts.get('REPLAY', 0)} REPLAY."
        else:
            other = "REPLAY" if selected_mode == "LIVE" else "LIVE"
            message = (f"0 {selected_mode} events match these filters. The database contains "
                       f"{counts.get(selected_mode, 0)} {selected_mode} and {counts.get(other, 0)} {other} events.")
    return {"mode": selected_mode, "events": result, "counts_by_mode": counts,
            "message": message, "limit": limit, "offset": offset}


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
            "weather": {"refresh_interval_s": float(CONFIG["weather"]["refresh_interval_s"])},
            "map_zoom": int(CONFIG.get("web", {}).get("map_zoom", 9)),
            "altitude_bands_m": CONFIG.get("web", {}).get("altitude_bands_m", {}),
            "stale_after_s": float(CONFIG.get("web", {}).get("stale_after_s", 90)),
            "age_refresh_s": float(CONFIG.get("web", {}).get("age_refresh_s", 1)),
            "reconnect_initial_ms": int(CONFIG.get("web", {}).get("reconnect_initial_ms", 1000)),
            "reconnect_max_ms": int(CONFIG.get("web", {}).get("reconnect_max_ms", 15000)),
            "low_altitude_ft": float(CONFIG["collector"]["low_altitude_ft"]),
            "labels_min_zoom": int(CONFIG["web"].get("labels_min_zoom", 11)),
            "prediction_ticks_min_zoom": int(CONFIG["web"].get("prediction_ticks_min_zoom", 11)),
            "inferred_display_s": float(CONFIG["airport_activity"]["inferred_display_s"]),
            "runway_badge_limit": int(CONFIG["airport_activity"]["runway_badge_limit"]),
            "label_exclusion_nm": float(CONFIG["web"].get("label_exclusion_nm", 2)),
            "trail_oldest_opacity": float(CONFIG["web"].get("trail_oldest_opacity", 0.18)),
            "trail_newest_opacity": float(CONFIG["web"].get("trail_newest_opacity", 0.82)),
            "replay": CONFIG["replay"], "cpa_display": {
                "near_nm": float(CONFIG["cpa"]["display_near_nm"]),
                "amber_nm": float(CONFIG["cpa"]["display_amber_nm"]),
            }}


@app.get("/api/weather")
async def live_weather():
    """Latest provider-backed weather; absent reports remain explicitly unavailable."""
    service = getattr(app.state, "weather_service", None)
    if service is None:
        return {"airport": CENTER.icao, "provider": CONFIG["weather"]["provider"],
                "status": "CHECKING", "metar": {"status": "CHECKING", "report": None},
                "taf": {"status": "CHECKING", "report": None},
                "message": "Waiting for the live weather provider."}
    return service.snapshot()


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


@app.get("/api/aircraft/{icao24}/info")
async def aircraft_info(icao24: str):
    collector = app.state.active_collector
    state = next((item for item in collector.manager.snapshot(collector.clock.now())
                  if item["icao24"].lower() == icao24.lower()), None)
    if state is None or state.get("latitude") is None or state.get("longitude") is None:
        raise HTTPException(404, "Aircraft is not currently reporting a position.")
    if state.get("on_ground") is True or (state.get("distance_nm") or 0) > float(AIRPORT["radius_nm"]):
        raise HTTPException(422, "Reference enrichment is limited to airborne aircraft inside the monitoring radius.")
    if not CONFIG["enrichment"].get("enabled", False):
        return {"mode": collector.mode, "airline": "Unknown",
                "route": {"available": False, "label": "Route unavailable", "reason": "Enrichment is disabled."},
                "aircraft": {"available": False, "reason": "Enrichment is disabled."}}
    service = app.state.enrichment_service
    return {"mode": collector.mode, "eta_uncertainty_fraction":float(CONFIG["enrichment"]["eta_uncertainty_fraction"]),
            **await service.info(callsign=state.get("callsign") or "", icao24=state["icao24"])}


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


def occupancy_coverage_gate():
    settings = CONFIG.get("occupancy", {})
    output = CONFIG["coverage_report"].get("output_dir", "reports/coverage")
    path = Path(output)
    if not path.is_absolute():
        path = ROOT / path
    summary_path = path / "coverage_summary.json"
    if not summary_path.exists():
        return {"assessable": False, "reason": "No measured coverage report is available.", "numbers": None}
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"assessable": False, "reason": "Measured coverage report could not be read.", "numbers": None}
    hours = float(summary.get("recording", {}).get("observed_hours", 0))
    low_rate = summary.get("runway_buffer_low_altitude_reports_per_hour")
    ground_rate = summary.get("runway_buffer_on_ground_reports_per_hour")
    buffer_rate = summary.get("runway_buffer_reports_per_hour")
    threshold_hours = float(settings.get("minimum_recording_hours", 6))
    thresholds = {"recording_hours": threshold_hours,
                  "low_altitude_buffer_reports_per_hour": float(settings.get("minimum_low_altitude_buffer_reports_per_hour", 5)),
                  "on_ground_buffer_reports_per_hour": float(settings.get("minimum_on_ground_buffer_reports_per_hour", 5))}
    # Older Phase 8 summaries expose airport-wide low/ground rates, not runway-buffer rates.
    # They are not sufficient evidence for assessability, so fail closed.
    numbers = {"recording_hours": hours, "low_altitude_reports_per_hour_inside_buffer": low_rate,
               "on_ground_reports_per_hour_inside_buffer": ground_rate, "runway_buffer_reports_per_hour": buffer_rate,
               "runways_available": bool(summary.get("runways_available")), "thresholds": thresholds}
    ok = (numbers["runways_available"] and hours >= threshold_hours and low_rate is not None and ground_rate is not None
          and buffer_rate is not None and low_rate >= thresholds["low_altitude_buffer_reports_per_hour"]
          and ground_rate >= thresholds["on_ground_buffer_reports_per_hour"]
          and buffer_rate >= thresholds["on_ground_buffer_reports_per_hour"])
    return {"assessable": bool(ok), "numbers": numbers,
            "reason": "Measured coverage meets the configured experimental gate." if ok else
            "Runway occupancy not assessable with current data: measured recording duration, on-ground/runway-buffer coverage, or runway geometry is below the configured gate."}


@app.get("/api/approaches")
async def approaches():
    collector = app.state.active_collector
    return {"mode": collector.mode, "updated_at": collector.clock.isoformat(),
            "approaches": collector.current_approaches,
            "recorded_tracks": await asyncio.to_thread(app.state.approach_store.query, collector.mode)}


@app.get("/api/airport-activity")
async def airport_activity(mode: str | None = None, start: float | None = None, end: float | None = None,
                           limit: int = Query(default=500, ge=1, le=2000)):
    collector=app.state.active_collector
    selected_mode=(mode or collector.mode).upper()
    if selected_mode not in {"LIVE","REPLAY","ALL"}:
        raise HTTPException(422,"mode must be LIVE, REPLAY, or ALL")
    modes=("LIVE","REPLAY") if selected_mode=="ALL" else (selected_mode,)
    records=[]
    for value in modes:
        records.extend(await asyncio.to_thread(app.state.activity_store.query,value,start,end,limit))
    records.sort(key=lambda item:item["time_ts"],reverse=True)
    return {"mode":selected_mode,"activities":records[:limit],"observed_ground_tracking_available":False,
            "note":"Airport activity records marked inferred are estimates at the last observed ADS-B position; no ground position is created."}


@app.get("/api/runway-status")
async def runway_status():
    collector = app.state.active_collector
    gate = occupancy_coverage_gate()
    occupancy = collector.current_occupancy
    by_end = {}
    window_s = float(CONFIG["approach"].get("runway_in_use_window_min", 20)) * 60
    recent_tracks = await asyncio.to_thread(app.state.approach_store.query, collector.mode, collector.clock.now()-window_s)
    eligible_states = {"LIKELY_APPROACHING", "NEAR_THRESHOLD", "PASSED_THRESHOLD_ZONE"}
    evidence = {}
    for item in recent_tracks:
        if item.get("state") in eligible_states:
            end = item.get("runway_end")
            evidence[end] = evidence.get(end, 0) + 1
    for item in collector.current_approaches:
        end = item["runway_end"]
        by_end.setdefault(end, []).append(item)
    runway_in_use = {"estimate": "unknown", "evidence_count": 0,
                     "window_s": window_s, "reason": "Not enough confirmed approach observations in the configured window."}
    eligible = [(end, count) for end, count in evidence.items()
                if count >= int(CONFIG["approach"].get("runway_in_use_min_approaches", 2))]
    if eligible:
        end, count = max(eligible, key=lambda row: row[1])
        runway_in_use = {"estimate": end, "evidence_count": count, "window_s": window_s,
                         "reason": "Estimate from current approach observations; not an official runway-in-use source."}
    return {"mode": collector.mode, "updated_at": collector.clock.isoformat(),
            "runway_in_use": runway_in_use, "approaches_by_end": by_end,
            "occupancy_assessable": gate["assessable"], "coverage_gate": gate,
            "experimental_occupancy": occupancy,
            "runway_ends": sorted({end.identifier for rw in collector.runway_definitions for end in (rw.end_a, rw.end_b)})}


@app.get("/api/runways")
async def runways():
    try:
        definitions = await asyncio.to_thread(runway_definitions)
    except RunwayDataError as error:
        raise HTTPException(503, str(error)) from error
    from ..geometry import local_transformers
    _forward, inverse = local_transformers(float(AIRPORT["latitude"]), float(AIRPORT["longitude"]))
    return {"airport": CENTER.icao, "runways": [item.as_dict(inverse_transformer=inverse) for item in definitions]}


@app.get("/api/frequencies")
async def airport_frequencies():
    settings = CONFIG["frequencies"]
    try:
        data = await asyncio.to_thread(load_frequencies, airport_ident=CENTER.icao,
            data_path=ROOT / settings["data_file"], override_path=ROOT / settings["override_file"])
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"airport": CENTER.icao, **estimate_facility_roles(data, settings.get("departure_fallback_type", "APP"))}


@app.get("/api/radio-estimates")
async def radio_estimates():
    collector = app.state.active_collector
    settings = CONFIG["frequencies"]
    try:
        data = await asyncio.to_thread(load_frequencies, airport_ident=CENTER.icao,
            data_path=ROOT / settings["data_file"], override_path=ROOT / settings["override_file"])
    except FileNotFoundError as error:
        raise HTTPException(503, str(error)) from error
    frequency_data = estimate_facility_roles(data, settings.get("departure_fallback_type", "APP"))
    states = collector.manager.snapshot(collector.clock.now())
    result = estimate_aircraft_facilities(states, collector.current_approaches,
        frequency_data, CONFIG["radio_estimate"])
    return {"airport": CENTER.icao, "mode": collector.mode, **result}


@app.get("/api/reference/airports/{code}")
async def airport_reference_lookup(code: str):
    settings = CONFIG["airport_reference"]
    index = AirportReferenceIndex(ROOT / settings["data_file"], ROOT / settings["database_file"])
    try:
        result = await asyncio.to_thread(index.lookup, code)
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if result is None:
        raise HTTPException(status_code=404, detail=f"No airport reference match for {code.upper()}")
    return result


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
            "recommendations": data.get("recommendations"),
            "runway_buffer_low_altitude_reports_per_hour": data.get("runway_buffer_low_altitude_reports_per_hour"),
            "runway_buffer_on_ground_reports_per_hour": data.get("runway_buffer_on_ground_reports_per_hour")}


@app.get("/api/replay/status")
async def replay_status():
    source = app.state.replay_source
    session_id = None if source is None or not source._times else f"{source._times[0]:.3f}"
    return {"mode": app.state.active_collector.mode, "running": source is not None,
            "completed_cycles": 0 if source is None else source._index,
            "total_cycles": 0 if source is None else source.total_cycles,
            "summary_job": None if session_id is None else await asyncio.to_thread(app.state.replay_summary_store.job, session_id)}


@app.get("/api/replay/sessions")
async def replay_sessions():
    gap_limit = float(CONFIG.get("replay_ui", {}).get("session_gap_s", 300))
    db_path = app.state.database_path
    def read_sessions():
        import sqlite3
        with sqlite3.connect(db_path) as db:
            times = [float(row[0]) for row in db.execute("SELECT sample_time FROM coverage_samples ORDER BY sample_time")]
            cycle_counts = {float(t): (int(a), int(low), int(ground)) for t,a,low,ground in db.execute(
                "SELECT sample_time,aircraft_count,below_threshold_count,on_ground_count FROM coverage_samples ORDER BY sample_time")}
        groups=[]
        for ts in times:
            if not groups or ts-groups[-1][-1] > gap_limit: groups.append([ts])
            else: groups[-1].append(ts)
        output=[]
        with sqlite3.connect(db_path) as db:
            for index, group in enumerate(groups):
                start_ts,end_ts=group[0],group[-1]
                distinct=db.execute("SELECT COUNT(DISTINCT icao24) FROM raw_states WHERE fetch_time>=? AND fetch_time<=?",(start_ts,end_ts)).fetchone()[0]
                peak=max((cycle_counts.get(ts,(0,0,0))[0] for ts in group),default=0)
                output.append({"session_id":f"{start_ts:.3f}","start_time":start_ts,"end_time":end_ts,
                    "duration_s":end_ts-start_ts,"poll_cycles":len(group),"distinct_aircraft":int(distinct or 0),
                    "peak_aircraft":peak,"gaps":sum(1 for a,b in zip(group,group[1:]) if b-a>float(CONFIG["coverage_report"]["max_poll_gap_s"])),
                    "low_altitude_reports":sum(cycle_counts.get(ts,(0,0,0))[1] for ts in group),
                    "on_ground_reports":sum(cycle_counts.get(ts,(0,0,0))[2] for ts in group),
                    "alerts_stored":int(db.execute("SELECT COUNT(*) FROM events WHERE mode='REPLAY' AND first_seen_ts>=? AND first_seen_ts<=?",(start_ts,end_ts)).fetchone()[0]),
                    "data_quality":"recorded observations"})
        return output
    return {"sessions": await asyncio.to_thread(read_sessions), "session_gap_s": gap_limit, "source":"app-recorded LIVE poll cycles"}


@app.get("/api/replay/cycles")
async def replay_cycles(start: float, end: float, limit: int = Query(default=200, ge=1, le=2000), offset: int = Query(default=0, ge=0)):
    if start > end: raise HTTPException(422, "start must be earlier than end")
    def read_cycles():
        import sqlite3
        with sqlite3.connect(app.state.database_path) as db:
            total=db.execute("SELECT COUNT(*) FROM coverage_samples WHERE sample_time>=? AND sample_time<=?",(start,end)).fetchone()[0]
            rows=db.execute("""SELECT sample_time,aircraft_count,below_threshold_count,on_ground_count
                FROM coverage_samples WHERE sample_time>=? AND sample_time<=? ORDER BY sample_time LIMIT ? OFFSET ?""",
                (start,end,limit,offset)).fetchall()
            result=[]
            for ts,count,low,ground in rows:
                result.append({"time":ts,"aircraft":count,"low_altitude":low,"on_ground":ground,
                    "pairs":None,"alerts_opened":None})
            return result,total
    cycles,total=await asyncio.to_thread(read_cycles)
    summaries,_=await asyncio.to_thread(app.state.replay_summary_store.query,start,end,limit,offset)
    by_time={item["time"]:item for item in summaries}
    for item in cycles:
        derived=by_time.get(item["time"])
        if derived:
            item.update(derived)
    return {"cycles":cycles,"limit":limit,"offset":offset,"total":total,
            "derived_summary_cached":bool(summaries),
            "note":"Pair and alert summaries are populated as the selected recorded range is processed through replay."}


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
    session_id = f"{source._times[0]:.3f}"
    await asyncio.to_thread(app.state.replay_summary_store.begin, session_id, source._times[0], source._times[-1],
                            source.total_cycles, source._times[0])
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
