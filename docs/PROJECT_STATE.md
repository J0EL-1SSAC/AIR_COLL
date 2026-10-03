# AIR_COL project state — Phase 3

Research/educational live ADS-B prototype for one airport. It is not ATC, TCAS/ACAS, or a certified safety system. The running application uses only OpenSky live states; source failure yields degraded status and no synthetic data.

## Folder tree

```text
AIR_COL/
├── .env.example
├── .gitignore
├── config.yaml
├── requirements.txt
├── backend/airwatch/
│   ├── cli.py
│   ├── collector.py
│   ├── models.py
│   ├── opensky.py
│   ├── state_manager.py
│   ├── storage.py
│   └── api/{__init__.py,app.py}
├── frontend/{index.html,package.json,package-lock.json,src/main.jsx,src/style.css}
├── tests/{test_config.py,test_enu_frame.py,test_state_manager.py,test_storage.py}
└── docs/PROJECT_STATE.md
```

## Run

From the project root, activate `.venv`, install `requirements.txt`, export `OPENSKY_CLIENT_ID` and `OPENSKY_CLIENT_SECRET`, then run `.venv/bin/uvicorn backend.airwatch.api.app:app --reload --reload-dir backend`. Restricting reload watching to backend code avoids scanning virtual environment packages. Run the browser UI from `frontend/` with `npm install && npm run dev`. The CLI remains `python -m backend.airwatch.cli --once` or continuous without `--once`; both paths record successful feed polls to SQLite.

## API and WebSocket contracts

- `GET /api/health`: `{status, updated_at, message}`.
- `GET /api/airport`: configured center, radius, ring, map and display settings.
- `GET /api/aircraft`: snapshot envelope with active positioned aircraft and recently lost records.
- `GET /api/coverage`: configured-window buckets with peak aircraft/below-threshold/on-ground counts and mean source data age.
- `/ws/live`: `{type, ts, source_status, data}` snapshots/status; data includes `aircraft`, counts, message, and `recently_lost`.
- Aircraft entries include raw SI fields, fetch/seen times, ENU x/y and velocity components, age, analysis-only age-compensated coordinates, bounded trail history, and quality flags.

## Configuration

`config.yaml` contains airport, OpenSky, polling/backoff, daily credit quota and estimated credits per request, state history/staleness/drop/tombstone thresholds, optional alpha-beta smoothing, SQLite path/batch/flush interval, coverage window/bucket, CORS and frontend map settings. Startup estimates credits/day and warns if it exceeds the configured quota. The estimate is only as accurate as the configured per-request cost; verify actual usage in the OpenSky account. Secrets remain environment variables. `AIR_COL_CONFIG` selects an alternate YAML file.

## Database

`data/airwatch.db` uses SQLite WAL. `raw_states` stores every returned OpenSky row payload, including null position/altitude/velocity, with UTC epoch fetch and source timestamps, source name, normalized fields, and quality flags. `coverage_samples` stores one successful-poll summary, including empty polls. A background writer batches inserts outside the collector poll path. `data/*.db` and SQLite sidecar files are ignored by Git.

## Known limitations

Coverage summaries reflect received public ADS-B feed samples and cannot establish complete airport traffic. The adapter logs rate-limit headers when present; HTTP 429 honors a retry-after-seconds header when provided and falls back to configured exponential backoff otherwise. Stale entries leave active state after the configured timeout and remain briefly in `recently_lost`; missing/incomplete states are stored but excluded from analysis. Optional smoothing is disabled by default. The dashboard shows trails only while aircraft remain active. No replay, trajectory prediction, conflict detection, or runway modeling exists yet.
