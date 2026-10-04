import asyncio
from types import SimpleNamespace

import httpx
import pytest

from backend.airwatch.api.app import app
from backend.airwatch.event_store import SQLiteEventStore
from tests.test_event_store import event


@pytest.fixture
def api_store(tmp_path, monkeypatch):
    store = SQLiteEventStore(tmp_path / "events.sqlite")
    store.initialize()
    asyncio.run(store.upsert_many([
        event("live-resolved", status="RESOLVED", risk="MEDIUM", timestamp=1000),
        event("live-active", status="ACTIVE", risk="CRITICAL", timestamp=2000),
        event("replay-resolved", mode="REPLAY", status="RESOLVED", risk="HIGH", timestamp=1500),
    ]))
    monkeypatch.setattr(app.state, "event_store", store, raising=False)
    monkeypatch.setattr(app.state, "active_collector", SimpleNamespace(mode="LIVE"), raising=False)
    return store


async def get(path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


def test_events_api_defaults_follow_current_mode_and_include_all_statuses(api_store):
    response = asyncio.run(get("/api/events"))
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "LIVE"
    assert {item["event_id"] for item in body["events"]} == {"live-resolved", "live-active"}
    assert body["counts_by_mode"] == {"LIVE": 2, "REPLAY": 1}


def test_events_api_filters_by_mode_status_risk_and_epoch_seconds(api_store, monkeypatch):
    by_mode = asyncio.run(get("/api/events?mode=REPLAY"))
    assert [item["event_id"] for item in by_mode.json()["events"]] == ["replay-resolved"]
    by_status = asyncio.run(get("/api/events?mode=ALL&status=resolved"))
    assert {item["event_id"] for item in by_status.json()["events"]} == {"live-resolved", "replay-resolved"}
    by_risk = asyncio.run(get("/api/events?mode=ALL&risk=critical"))
    assert [item["event_id"] for item in by_risk.json()["events"]] == ["live-active"]
    by_time = asyncio.run(get("/api/events?mode=ALL&start=1400&end=1600"))
    assert [item["event_id"] for item in by_time.json()["events"]] == ["replay-resolved"]


def test_events_api_all_mode_explains_empty_results(api_store):
    response = asyncio.run(get("/api/events?mode=LIVE&status=ESCALATED"))
    assert response.json()["events"] == []
    assert "0 LIVE events match" in response.json()["message"]
    assert "1 REPLAY" in response.json()["message"]


def test_events_api_defaults_to_replay_when_pipeline_is_replaying(api_store, monkeypatch):
    monkeypatch.setattr(app.state, "active_collector", SimpleNamespace(mode="REPLAY"), raising=False)
    body = asyncio.run(get("/api/events")).json()
    assert body["mode"] == "REPLAY"
    assert [item["event_id"] for item in body["events"]] == ["replay-resolved"]


def test_events_api_rejects_future_reversed_and_pre_recording_ranges(api_store):
    import time
    future=asyncio.run(get(f"/api/events?end={time.time()+3600}"))
    reversed_range=asyncio.run(get("/api/events?start=2000&end=1000"))
    before=asyncio.run(get("/api/events?start=1"))
    assert future.status_code==422 and "future" in future.json()["detail"]
    assert reversed_range.status_code==422 and "at or before" in reversed_range.json()["detail"]
    assert before.status_code==422 and "recorded data range" in before.json()["detail"]
