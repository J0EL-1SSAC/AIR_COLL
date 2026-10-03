# AIR_COL project state — Phase 4

Research/educational live ADS-B prototype for one configured airport. It is not ATC, TCAS/ACAS, or a certified safety system. Live aircraft data comes from OpenSky; replay reads only the app's recorded OpenSky rows. Feed errors and empty responses remain visibly degraded/no-data; the app has no synthetic aircraft fallback.

## Folder tree

```text
AIR_COL/
├── .env.example
├── .gitignore
├── config.yaml
├── requirements.txt
├── backend/airwatch/
│   ├── cli.py
│   ├── clock.py
│   ├── collector.py
│   ├── models.py
│   ├── opensky.py
│   ├── replay.py
│   ├── state_manager.py
│   ├── storage.py
│   └── api/{__init__.py,app.py}
├── frontend/{index.html,package.json,package-lock.json,src/main.jsx,src/style.css}
├── tests/{test_config.py,test_enu_frame.py,test_opensky_limits.py,test_replay.py,test_state_manager.py,test_storage.py}
└── docs/PROJECT_STATE.md
```

## Install and run

From the project root, activate `.venv` (Python 3.11+), install `python -m pip install -r requirements.txt`, and export `OPENSKY_CLIENT_ID` and `OPENSKY_CLIENT_SECRET` in the shell. Create OpenSky OAuth client credentials in the OpenSky account portal's API client/credentials area; use the client ID and client secret values as the two environment variables. Never put credentials in YAML or commit them.

Start the backend with `.venv/bin/uvicorn backend.airwatch.api.app:app --reload --reload-dir backend`. Start the React dashboard in a second terminal with `cd frontend && npm install && npm run dev`. The live-only CLI still works with `python -m backend.airwatch.cli --once`; without credentials it reports `DEGRADED` and `NO DATA`, without inventing records. Continuous CLI mode records live polls until stopped.

Run tests with `.venv/bin/python -m pytest -q`; build the UI with `npm --prefix frontend run build`.

## API and WebSocket contracts

- `GET /api/health`: `{mode, status, updated_at, message}`. `mode` is `LIVE` or `REPLAY`.
- `GET /api/airport`: configured center/radius, map settings, altitude bands and replay speed settings.
- `GET /api/aircraft`: selected pipeline snapshot, including `mode` at the envelope top level.
- `GET /api/coverage`: recorded live coverage buckets; replay does not write to this dataset.
- `GET /api/replay/status`: selected mode and replay cycle progress.
- `POST /api/replay/start`: optional JSON `{start_time, end_time, speed}` where times are UTC Unix seconds. Omitting bounds replays all locally recorded OpenSky poll cycles. Speed defaults to `replay.default_speed` and is limited by configured min/max. Returns 404 if no recorded cycles match.
- `POST /api/replay/stop`: stops replay and switches the dashboard back to the continuing live collector.
- `/ws/live`: `{type, mode, ts, source_status, data}`. During replay, only replay snapshots/status are delivered. On start/end, connected clients receive the selected mode's snapshot.

The replay adapter implements the same `DataSource.fetch_states(center, radius_nm)` interface as the OpenSky adapter. It iterates `coverage_samples` poll times and loads exact matching `raw_states` rows marked `opensky`; it retains empty poll cycles and rechecks the configured geodesic radius. It never writes observations. `ReplayClock` advances at the recorded UTC source timestamps; `LiveClock` drives live collector age, staleness and polling. The live collector and its recorder continue running while replay data is displayed.

## Configuration

`config.yaml` contains airport identity/coordinates/radius, OpenSky OAuth/API endpoints and timeout/quota estimates, collector poll/backoff/sparsity thresholds, manager history/staleness/smoothing, SQLite path/batch/flush, coverage window/bucket, replay speed range/steps/queue, API CORS, and web map/display settings. `AIR_COL_CONFIG` selects an alternate YAML file. Secrets remain in environment variables.

## Database

`data/airwatch.db` uses SQLite WAL. `raw_states` stores every returned live OpenSky state row and source timestamp; `coverage_samples` stores successful poll summaries, including empty responses. `data/*.db` and SQLite sidecar files are ignored by Git. Replay reads those existing tables only; no replay event or state writes occur in this phase.

## Recorded data available when Phase 4 was implemented

At the latest check, the database contained 263 `raw_states` rows across 56 coverage cycles, 12 distinct aircraft, from `2026-10-03T10:12:24.461015+00:00` to `2026-10-03T10:34:17.208908+00:00` (about 22 minutes). This is enough to exercise the replay controls and source, but not to assess traffic patterns or sparse coverage. Record several hours spanning busy and quiet periods before drawing research conclusions.

## Known limitations

Replay bounds are supplied as Unix seconds through the API; the dashboard currently replays the full locally recorded range and offers playback-speed selection. Replay has independent in-memory state and does not write replayed states into `raw_states`. The frontend does not label imported external history because importing is not implemented. Public ADS-B can be delayed, omit aircraft, and have poor low-altitude/ground coverage. Coverage counts describe only received public feed data. This remains a research prototype and does not generate or confirm conflict/runway events.
