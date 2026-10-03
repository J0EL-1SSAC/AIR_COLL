# AIR_COL project state — Phase 5

Research and education prototype for live ADS-B around one configured airport. It is not ATC, TCAS/ACAS, or a certified safety system. The application uses live OpenSky data and replays only observations that it recorded; unavailable or empty feeds produce degraded/no-data status, never synthetic aircraft.

## Folder tree

```text
AIR_COL/
├── .env.example
├── .gitignore
├── config.yaml
├── requirements.txt
├── backend/airwatch/
│   ├── api/{__init__.py,app.py}
│   ├── cli.py
│   ├── clock.py
│   ├── collector.py
│   ├── models.py
│   ├── opensky.py
│   ├── prediction.py
│   ├── replay.py
│   ├── state_manager.py
│   └── storage.py
├── frontend/{index.html,package.json,package-lock.json,src/main.jsx,src/style.css}
├── tests/{test_config.py,test_enu_frame.py,test_opensky_limits.py,test_prediction.py,test_replay.py,test_state_manager.py,test_storage.py}
└── docs/PROJECT_STATE.md
```

## Install and run

From the project root, activate `.venv` (Python 3.11+), install `python -m pip install -r requirements.txt`, and export `OPENSKY_CLIENT_ID` and `OPENSKY_CLIENT_SECRET`. Create OAuth client credentials in the OpenSky account's API credentials area. Keep both values in environment variables; do not put secrets in YAML or commit them.

Start the API with `.venv/bin/uvicorn backend.airwatch.api.app:app --reload --reload-dir backend`. Start the UI in a second terminal with `cd frontend && npm install && npm run dev`. The live CLI remains `python -m backend.airwatch.cli --once` or continuous without `--once`; both use only the live source. Missing credentials produce `DEGRADED` / `NO DATA`.

Tests: `.venv/bin/python -m pytest -q`. Frontend build: `npm --prefix frontend run build`.

## API and WebSocket contracts

- `GET /api/health`: `{mode, status, updated_at, message}`, with mode `LIVE` or `REPLAY`.
- `GET /api/airport`: configured airport/radius, map settings, replay and prediction settings.
- `GET /api/aircraft`: currently selected pipeline snapshot and mode.
- `GET /api/predictions`: batch constant-velocity trajectories or explicit per-aircraft skip reason codes, plus mode/model/horizons.
- `GET /api/aircraft/{icao24}/prediction`: one current aircraft's prediction; 404 if no longer active.
- `GET /api/coverage`: recorded live-feed coverage buckets.
- Replay controls: `GET /api/replay/status`, `POST /api/replay/start` with optional `{start_time,end_time,speed}` (Unix seconds), and `POST /api/replay/stop`.
- `/ws/live`: `{type, mode, ts, source_status, data}`. Predictions are intentionally fetched through the batch REST endpoint to keep WebSocket snapshots compact.

Prediction inputs are age-compensated ENU positions from the active state manager and velocity components computed with `vx=speed*sin(track)`, `vy=speed*cos(track)` for true track clockwise from north. `ConstantVelocityPredictor` is selected by config and implements the `Predictor` protocol. It returns ENU and latitude/longitude at configured horizons, vertical estimates clamped to configured airport elevation, uncertainty radii, or a reason code when prediction is skipped. The same injected LIVE/REPLAY clock computes report age. The forward and inverse pyproj transforms use x=east, y=north internally and return longitude/latitude in API order.

## Configuration

`config.yaml` holds airport identity/coordinates/radius; OpenSky endpoints, timeout, and quota; collector polling/backoff; state-manager history, quality and staleness; database path/batching; coverage; replay speed; prediction model, horizons, skip rules, ground elevation, and uncertainty terms; API CORS; and web display settings. Secrets are environment-only. VOMM ground elevation is set to 16.4592 m (54 ft) from the Airports Authority of India eAIP; verify the current value against official charts before research use. No dependency was added for Phase 5.

## Database

`data/airwatch.db` uses SQLite WAL. `raw_states` stores live OpenSky observations; `coverage_samples` stores successful poll summaries, including empty polls. Replay reads those recorded rows only and does not write replayed data. `data/*.db` and SQLite sidecars are ignored by Git. Predictions are computed from active in-memory LIVE or REPLAY state and are not persisted.

## Known limitations

Prediction uses straight constant velocity and heading: it does not model turns, wind, pilot intent, or calibrated uncertainty. Geographic/pressure altitude may be missing or differ in datum; geometric altitude is preferred, with barometric altitude as fallback. Ground elevation is an airport-level clamp, not local terrain/runway elevation. Stale, low-quality, on-ground, or incomplete states return explicit skip codes. Public ADS-B may be delayed, omit aircraft, and have poor low-altitude/ground coverage. All results are research estimates and cannot confirm conflict or runway events.
