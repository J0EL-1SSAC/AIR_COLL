# AIR_COL

AIR_COL is a research and education prototype for observing public ADS-B aircraft around one configurable airport. The default airport is Chennai International (VOMM). It currently includes live OpenSky collection, a React + Leaflet dashboard, recording and replay, trajectory prediction, aircraft-pair closest-approach estimates, configurable risk profiles, and a persistent alert lifecycle.

> **Research use only.** AIR_COL is not ATC, TCAS/ACAS, a certified safety system, or an official separation determination. Public ADS-B data can be delayed, incomplete, or inaccurate, especially at low altitude and on the ground. Do not use it for operational or safety decisions.

The running application uses real OpenSky data only. It never substitutes demo aircraft. When credentials or the live source are unavailable, it reports a degraded/no-data status. Replay and offline evaluation read observations previously recorded from the live feed.

## Current features

- Configurable airport, monitoring radius, polling/backoff, data-quality and staleness rules.
- OpenSky OAuth2 client-credentials adapter behind a `DataSource` interface.
- FastAPI REST API and WebSocket stream for LIVE and REPLAY pipeline modes.
- React + Vite + Leaflet dashboard with aircraft, trails, predictions, closest approaches, alert highlights, and event history.
- Age-compensated aircraft state management in a local ENU frame.
- SQLite recording for raw observations, coverage samples, and persistent alert events.
- Replay of recorded observations through the same state, pair, risk, and alert pipeline.
- CPA calculations and configurable LOW/MEDIUM/HIGH/CRITICAL risk profiles.
- Alert confirmation, hysteresis, escalation, cooldown continuation, and `data_lost` resolution.
- Headless evaluation of recorded ranges, with CSV and Markdown reports.
- Runway-end model with ENU core/buffer polygons, extended centerlines, and approach corridors (uses a user-supplied runway CSV).
- Read-only surveillance coverage report with altitude/distance bands, report age, fade-out heuristics, and runway coverage when runway geometry is available.

Runway occupancy and runway conflict detection are not implemented yet. Runway geometry and coverage are research aids and require verification against official charts.

## Requirements

- Python 3.11 or newer
- Node.js 18 or newer and npm
- OpenSky Network OAuth2 client credentials for live data

All operational thresholds and airport settings are in `config.yaml`. Secrets belong in environment variables, never in the repository.

## OpenSky credentials

Create API client credentials in your OpenSky Network account. Set the client ID and secret in the shell that starts the backend:

```bash
export OPENSKY_CLIENT_ID='your-client-id'
export OPENSKY_CLIENT_SECRET='your-client-secret'
```

The configured token endpoint uses the `opensky-network` realm. OpenSky quota and credit behavior depends on account and request details. The application logs an estimate based on configured polling and credit settings; check your OpenSky account for actual usage.

## Install

From the repository root on macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm --prefix frontend install
```

## Run the dashboard

Start the API in one terminal from the repository root:

```bash
source .venv/bin/activate
export OPENSKY_CLIENT_ID='your-client-id'
export OPENSKY_CLIENT_SECRET='your-client-secret'
.venv/bin/uvicorn backend.airwatch.api.app:app --reload --reload-dir backend
```

Start the frontend in a second terminal:

```bash
npm --prefix frontend run dev
```

Open the local URL printed by Vite, usually `http://localhost:5173`. Missing credentials, API errors, and empty responses are shown as degraded/no-data; no synthetic aircraft are generated.

### Risk profiles

The default profile is `research_default`. To select the deliberately loose profile for exercising the alert lifecycle with real live or recorded observations, export this before starting the API:

```bash
export AIR_COL_RISK_PROFILE=sensitive_test
.venv/bin/uvicorn backend.airwatch.api.app:app --reload --reload-dir backend
```

The dashboard identifies the active profile and warns that `sensitive_test` alerts are not research results. The thresholds in every profile are research/demo values, not official ATC separation standards. Confirmation requires two qualifying poll cycles and at least 30 seconds by default; with the default 30-second poll interval, this typically adds about one interval after the first qualifying sample.

## Run the live-data CLI

From the repository root, with the virtual environment active and credentials exported:

```bash
python -m backend.airwatch.cli --once
```

Omit `--once` to continue polling. The API and CLI use the shared collector and record successful source polls.

## Replay and evaluation

The API exposes replay controls for ranges recorded in `raw_states`. Replay is tagged `REPLAY`, kept separate from LIVE event queries, and does not write replayed observations back into the raw live-state table.

To evaluate a recorded time range without running the API:

```bash
.venv/bin/python scripts/evaluate_alerts.py \
  --start 2026-10-03T10:00:00Z \
  --end 2026-10-03T11:00:00Z \
  --profile research_default \
  --db data/airwatch.db \
  --output-dir reports/alert_evaluation
```

The script writes `alert_events.csv` and `alert_evaluation.md`, including cycle and aircraft-hour counts, alert rates by peak risk, duration statistics, data-loss resolutions, confidence share, and closest pairs. It reads recorded observations and does not modify the live raw-state database. For normal traffic, tune toward a low false-alert rate; the goal is not to find conflicts. Treat `sensitive_test` results as lifecycle/UI checks, not research findings.

## Runway data and coverage report

The application does not download runway data. Download `runways.csv` manually from the [OurAirports data downloads page](https://ourairports.com/data/) and save it as `data/runways.csv` in the project root. The dataset is public-domain but has no guarantee of accuracy or fitness for use. The optional `data/runways_override.yaml` file supports documented corrections; each applied override is logged.

Set `runways.magnetic_variation_deg` in `config.yaml` to the current VOMM variation for the data/chart effective date. The default zero is treated as unconfirmed and produces a warning. Positive east variation follows `true heading = magnetic heading + variation`. Run the verification script and compare every threshold, length, width, and heading with the official AIP or aerodrome chart:

```bash
.venv/bin/python scripts/verify_runways.py
```

The dashboard requests `/api/runways`; if the CSV is missing or contains no VOMM runway rows, the UI reports that and draws no substitute geometry. Runway core is on by default; buffer and approach corridor layers are off by default. Click a runway to inspect its source metadata.

Generate the read-only report from observations already in SQLite:

```bash
.venv/bin/python scripts/coverage_report.py \
  --db data/airwatch.db --output-dir reports/coverage
```

Optional UTC bounds use `--start 2026-10-03T10:00:00Z --end 2026-10-03T11:00:00Z`. Outputs include Markdown, a compact JSON summary, and CSVs for altitude/distance bands, hourly coverage, fade-out tracks and histograms, update intervals, runway buffers, and approach corridors. Without runway geometry the script still reports non-runway metrics, marks runway metrics unavailable, and never infers missing aircraft. Short recordings are labeled insufficient data using thresholds in `config.yaml`.

## API and WebSocket

- `GET /api/health` — mode, source status, latest update, active risk profile.
- `GET /api/airport` — airport center, monitoring radius, map and display settings.
- `GET /api/aircraft` — current selected-mode aircraft states.
- `GET /api/predictions` and `GET /api/aircraft/{icao24}/prediction` — prediction results and skip reasons.
- `GET /api/pairs` — CPA metrics and pair-filter counts.
- `GET /api/risk/config` — active profile and thresholds.
- `GET /api/alerts/active?mode=LIVE|REPLAY` — active alerts for the selected mode.
- `GET /api/events` — event history; filters include `status`, `risk`, `mode`, `aircraft`, `start`, `end`, `limit`, and `offset`. Mode defaults to LIVE.
- `GET /api/events/{event_id}` — complete event details and state snapshots.
- `GET /api/coverage` — recorded coverage summaries.
- `GET /api/runways` — runway metadata and core, buffer, centerline, and approach-corridor GeoJSON in longitude/latitude order; returns an actionable error if the runway file is missing.
- `GET /api/coverage/runway-summary` — headlines from the latest generated report (404 until a report exists).
- `GET /api/replay/status`, `POST /api/replay/start`, `POST /api/replay/stop` — replay controls.
- `WS /ws/live` — aircraft/status snapshots plus `type: "alert"` messages with `opened`, `updated`, `escalated`, or `resolved` sub-events.

Example:

```bash
curl http://127.0.0.1:8000/api/risk/config
curl 'http://127.0.0.1:8000/api/events?mode=LIVE&limit=20'
```

## Data storage

The default database is `data/airwatch.db` (ignored by Git), using SQLite. It contains:

- `raw_states` — raw/normalized source observations and fetch timestamps.
- `coverage_samples` — received aircraft counts and coverage summaries per poll.
- `events` — alert lifecycle, LIVE/REPLAY mode, profile, current and worst CPA values, reasons, confidence, resolution, and aircraft snapshots.

Event queries default to LIVE so replay events are not mixed into live history. Event timestamps use UTC epoch seconds. The event table is initialized and migrated by the service.

## Configuration

Edit `config.yaml` for airport coordinates/radius, OpenSky endpoints, polling/backoff, state quality/staleness, storage, replay, predictions, CPA, web display, runway model/validation/geometry, coverage analysis, and risk settings. Risk settings include named profiles, separation/time thresholds, filtering and adjustment policies, confirmation and clear hysteresis, cooldown, data-loss timeout, and confidence thresholds. `AIR_COL_CONFIG` can point to an alternate YAML file. Keep OAuth secrets in environment variables.

## Tests and build

```bash
source .venv/bin/activate
python -m pytest -q
python -m compileall -q backend tests scripts
npm --prefix frontend run build
```

Tests use isolated numeric inputs and temporary databases; test records are not imported by the running application.

## Limitations

OpenSky coverage is incomplete and reported positions may be stale. CPA and trajectory predictions use simple constant-velocity models. Risk levels are configurable research estimates and have not been validated as operational criteria. OurAirports runway records may be wrong or stale; heading checks use the manually configured magnetic variation and are warnings only. Fade-out classification is heuristic and reports the last/first received observation, not an actual aircraft disappearance. Alerts are not operational warnings and must not be used for ATC, collision avoidance, or runway decisions.
