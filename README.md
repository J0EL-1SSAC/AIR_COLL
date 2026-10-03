# AIR_COL

AIR_COL is a research and educational prototype for observing live ADS-B aircraft around one configurable airport. The default airport is Chennai International (VOMM). The current implementation includes live OpenSky collection, an interactive Leaflet dashboard, aircraft state/history management, SQLite recording, and coverage summaries.

> **Research use only.** AIR_COL is not ATC, TCAS/ACAS, or a certified runway-safety system. Public ADS-B data can be delayed, omit aircraft, and have poor low-altitude and ground coverage. Do not use it for operational or safety decisions.

The running application uses real OpenSky data only. It never substitutes demo aircraft. When credentials or the live source are unavailable, the app reports `DEGRADED` or `NO_DATA`.

## Current scope

Implemented through Phase 3:

- OpenSky OAuth2 client-credentials adapter, with a `DataSource` interface for later live sources.
- Configurable airport, radius, polling, backoff, credit estimate, history and quality thresholds.
- FastAPI REST endpoints and a WebSocket stream.
- React + Vite + Leaflet map with live positions and trails.
- In-memory aircraft state/history manager with ENU coordinates, age compensation, quality flags, and bounded history.
- SQLite WAL recording of raw source states and coverage samples.
- Bucketed coverage summaries.

Replay, trajectory prediction, potential conflict detection, and runway modeling are not implemented yet.

## Requirements

- Python 3.11 or newer
- Node.js 18 or newer and npm
- OpenSky Network OAuth2 client credentials for live data

The project has been exercised with Python 3.14 and Node.js 26. See `config.yaml` for the active settings.

## OpenSky credentials

Create an API client in your OpenSky Network account and obtain its OAuth2 client ID and client secret. Set both as environment variables in the same shell that starts the backend. Do not commit credentials or put them in frontend variables.

```bash
export OPENSKY_CLIENT_ID='your_client_id'
export OPENSKY_CLIENT_SECRET='your_client_secret'
```

The configured token endpoint uses the `opensky-network` realm. OpenSky quota and credit behavior can depend on account and request details. The startup estimate uses `opensky.daily_credit_quota` and `opensky.estimated_credits_per_states_request`; verify actual usage in your account.

## Install

From the repository root on macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

cd frontend
npm install
cd ..
```

## Run the dashboard

Start the API in one terminal from the repository root:

```bash
source .venv/bin/activate
export OPENSKY_CLIENT_ID='your_client_id'
export OPENSKY_CLIENT_SECRET='your_client_secret'
.venv/bin/uvicorn backend.airwatch.api.app:app --reload --reload-dir backend
```

Start the frontend in a second terminal:

```bash
cd frontend
npm run dev
```

Open the local URL printed by Vite, usually `http://localhost:5173`. If the API is not running or cannot reach OpenSky, the interface shows a disconnected or degraded/no-data status and does not display invented aircraft.

## Run the live-data CLI

From the repository root, with the virtual environment active and credentials exported:

```bash
python -m backend.airwatch.cli --once
```

Omit `--once` to continue polling. The CLI and API use the same collector and both record successful source polls.

## API

- `GET /api/health` — collector status and latest update time.
- `GET /api/airport` — airport center, configured monitoring radius, and map settings.
- `GET /api/aircraft` — latest aircraft snapshot envelope.
- `GET /api/coverage` — recent bucketed aircraft counts, counts below the configured altitude threshold, on-ground counts, and mean data age.
- `WS /ws/live` — status and aircraft snapshot messages. Messages use `{type, ts, source_status, data}`.

`source_status` is `OK`, `DEGRADED`, or `NO_DATA`. The dashboard can additionally show `DISCONNECTED` when its WebSocket is unavailable.

To query coverage locally:

```bash
curl http://127.0.0.1:8000/api/coverage
```

## Data storage

The default database is `data/airwatch.db` (ignored by Git). It uses SQLite WAL mode and contains:

- `raw_states` — each state returned by the source, its raw payload, fetch time, source name, normalized fields, and quality flags. Null position, altitude, or velocity values are retained.
- `coverage_samples` — one summary per successful poll, including empty polls.

Times in the database are UTC epoch seconds. Database writing is batched on a background task so disk writes do not block the collector’s polling path.

## Configuration

Edit `config.yaml` to configure the airport ICAO and coordinates, radius, OpenSky endpoints, poll interval, backoff, estimated quota, history length, stale/drop times, optional alpha-beta smoothing, database path and batching, coverage window, and local CORS origins. Keep secrets in environment variables.

`AIR_COL_CONFIG` can point the API to an alternate YAML configuration file.

## Tests and build

```bash
source .venv/bin/activate
python -m pytest -q
python -m compileall -q backend tests
npm --prefix frontend run build
```

Tests use isolated numeric inputs or temporary databases; test data is not imported by the running application.

## Limitations

OpenSky coverage is incomplete and data age varies. Coverage measures received observations and cannot show aircraft the feed did not receive. The configured per-request credit cost is an estimate. Stale aircraft leave active state after the configured timeout and remain briefly in a last-seen record. This prototype does not yet perform conflict detection or runway incursion assessment.
