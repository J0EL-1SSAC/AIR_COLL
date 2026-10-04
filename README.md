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
- Experimental runway-end approach tracking with clock-based confirmation, ETA windows, confidence, and persistent `approach_tracks` records.
- Experimental runway occupancy observations that stay `UNKNOWN` until a runway-buffer-specific measured-coverage gate passes.
- Offline OurAirports frequency lookups and a lazily indexed airport reference database for ICAO/IATA code resolution.
- Live VOMM METAR and TAF retrieval through the AviationWeather.gov Data API, with provider/observation timestamps and explicit unavailable, stale, or degraded states. No forecast or weather fallback is generated locally.
- Bright side-tab dashboard for Overview, Aircraft, Approaches, Alerts, and Replay, using CARTO Voyager by default with OpenStreetMap and CARTO Dark Matter alternatives.

Runway occupancy is experimental and not validated by the current coverage record; runway conflict alerts remain future work. Runway geometry, frequencies and coverage are research aids and require verification against official charts.

### Live weather and airport frequencies

The Overview panel shows VOMM's current METAR and TAF from [AviationWeather.gov's Data API](https://aviationweather.gov/data/api/), including the provider report text, product timestamps, retrieval time, and age. METAR requests refresh at the configured `weather.refresh_interval_s` (default 60 seconds); TAF requests refresh at `weather.taf_refresh_interval_s` (default 600 seconds). If a report is absent, the provider request fails, or a report exceeds the configured freshness limit, the display says it is unavailable, degraded, or stale. Weather has no fabricated fallback. Public aviation weather reports can themselves lag the actual conditions.

Weather API contract: `GET /api/weather` returns overall status (`CHECKING`, `OK`, `DEGRADED`, or `NO_DATA`) and separate `metar` and `taf` products, each with `status`, `report`, `error`, fetch time and age. METAR/TAF reports are provider records; missing reports are `null`. Provider: AviationWeather.gov; its API is queried by ICAO station identifier. `config.yaml` contains the URLs, intervals, request timeout, report age limit and User-Agent. No additional dependency is needed (the project already uses httpx).

The Overview panel also shows the locally supplied OurAirports frequency records and labels them as reference data. Frequencies are not live transmissions and must be verified against the official AIP; a missing local CSV is displayed as unavailable.

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

Open the local URL printed by Vite, usually `http://localhost:5173`. CARTO/OSM tiles require internet access; check each provider's usage terms. Missing credentials, API errors, and empty responses are shown as degraded/no-data; no synthetic aircraft are generated.

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

The application does not download airport reference data. Download the needed CSVs from the [OurAirports data downloads page](https://ourairports.com/data/) and copy them into the project's `data/` directory:

```text
data/runways.csv
data/airport-frequencies.csv
data/airports.csv
data/navaids.csv  # not used yet
```

The repository ignores `data/*.csv`; these large local inputs are not committed. `config.yaml` points runway geometry to `data/runways.csv`, frequency lookup to `data/airport-frequencies.csv`, and the offline airport code index to `data/airports.csv`. The airport index is built lazily into ignored `data/airport_reference.db`; the frontend receives only a requested airport result, never the full CSV. The dataset is public-domain but has no guarantee of accuracy or fitness for use. The optional `data/runways_override.yaml` file supports documented runway corrections; each applied override is logged.

The optional `data/frequencies_override.yaml` corrects the VOMM OurAirports description typo `SCHENNAI RADARS` to `CHENNAI RADARS`; the override is logged. When a DEP frequency is absent, the service can show APP as a clearly labeled facility estimate only, not a verified departure frequency. All frequencies must be checked against the current official AIP.

Run `.venv/bin/python scripts/verify_frequencies.py` to print the local VOMM records and the departure/clearance fallback labels. `GET /api/frequencies` returns the same local reference data. `GET /api/reference/airports/VOMM` (or `/MAA`) lazily builds an indexed SQLite reference from the complete `airports.csv`, and returns only the matched record. Navaids are copied for future use but are not read in this phase.

The configured Chennai variation is the user-supplied WMM2025 value −1°4′ (−1.0666667°, west), checked 2026-10-04 from [Magnetic-Declination.com](https://www.magnetic-declination.com/India/Chennai/1136292.html); its page lists −1.07° (−1°4′) and WMM2025. `magnetic_variation_confirmed` is true. Positive east variation follows `true heading = magnetic heading + variation`. The runway endpoints and `heading_degT` source fields are also checked. Verify this city value against the applicable official VOMM AIP/chart effective date, then compare every physical end, displaced landing threshold, length, width, surface, and heading:

```bash
.venv/bin/python scripts/verify_runways.py
```

The dashboard requests `/api/runways`; if the CSV is missing or contains no VOMM runway rows, the UI reports that and draws no substitute geometry. Runway core is on by default; buffer and approach corridor layers are off by default. Click a runway to inspect its source metadata. OurAirports `*_displaced_threshold_ft` is interpreted as a distance from the recorded physical runway end toward the reciprocal end; approach corridors, threshold distance and along-track measurements use the resulting landing threshold. Verify this assumption and the recorded VOMM value against the official AIP.

Approach tracking uses true headings computed from threshold coordinates and the state manager's age-compensated ENU positions. The `approach` section in `config.yaml` controls heading, speed, glide/descent, freshness, movement window, confirmation, hysteresis, and runway-in-use evidence. ETA is a min–max window because samples may be 25–30 seconds apart and reports may be aged. Output is a research estimate and requires review against runway charts and recorded tracks.

The `occupancy` section configures experimental buffered-runway evidence and its coverage gate. Missing reports are not evidence of a clear runway. Rerun the coverage report after installing runway geometry to calculate runway-buffer low-altitude and ground rates.

Replay recorded data through the state and approach pipeline:

```bash
.venv/bin/python scripts/runway_stats.py --db data/airwatch.db --output-dir reports/runway_stats
```

Optional UTC bounds: `--start 2026-10-03T10:00:00Z --end 2026-10-03T11:00:00Z`. Outputs include JSON, per-hour CSV, and Markdown. Replayed observations are not written to `raw_states`.

All local `data/*.csv` downloads are excluded from Git. Generated coverage reports and the indexed airport-reference SQLite file are also ignored and can be regenerated from local inputs at any time.

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
- `GET /api/frequencies` — local frequency records and clearly labeled facility estimates when DEP/CLR entries are absent.
- `GET /api/reference/airports/{code}` — offline airport lookup by ICAO, IATA, or OurAirports ident, backed by a lazy indexed SQLite copy.
- `GET /api/frequencies` — local airport frequency rows and explicitly labeled facility-role estimate/fallback.
- `GET /api/reference/airports/{code}` — indexed offline lookup by ICAO, IATA, or OurAirports ident.
- `GET /api/coverage/runway-summary` — headlines from the latest generated report (404 until a report exists).
- `GET /api/approaches` — current approach evidence and persistent tracks for the selected LIVE/REPLAY mode.
- `GET /api/runway-status` — runway-use estimate, approach evidence, experimental occupancy state, and measured gate inputs.
- `GET /api/replay/sessions` — recorded LIVE poll sessions split at `replay_ui.session_gap_s`.
- `GET /api/replay/cycles?start=...&end=...&limit=...&offset=...` — read-only poll metrics; cycle-level pair counts are unavailable unless stored as derived records.
- `GET /api/replay/status`, `POST /api/replay/start`, `POST /api/replay/stop` — replay controls.
- `WS /ws/live` — aircraft/status snapshots plus `type: "alert"` messages with `opened`, `updated`, `escalated`, or `resolved` sub-events.

Example:

```bash
curl http://127.0.0.1:8000/api/risk/config
curl 'http://127.0.0.1:8000/api/events?mode=LIVE&limit=20'
curl http://127.0.0.1:8000/api/runways
curl http://127.0.0.1:8000/api/coverage/runway-summary
```

`/api/coverage/runway-summary` returns HTTP 404 until `scripts/coverage_report.py` has written a report. `/api/runways` returns HTTP 503 with instructions if the manual runway dataset is missing; it never substitutes built-in geometry.

## Data storage

The default database is `data/airwatch.db` (ignored by Git), using SQLite. The lazily built offline reference index uses separate ignored `data/airport_reference.db`. The main database contains:

- `raw_states` — raw/normalized source observations and fetch timestamps.
- `coverage_samples` — received aircraft counts and coverage summaries per poll.
- `events` — alert lifecycle, LIVE/REPLAY mode, profile, current and worst CPA values, reasons, confidence, resolution, and aircraft snapshots.
- `approach_tracks` — persistent approach state and latest measurements, tagged LIVE or REPLAY.

Event queries default to LIVE so replay events are not mixed into live history. Event timestamps use UTC epoch seconds. The event table is initialized and migrated by the service.

## Configuration

Edit `config.yaml` for airport coordinates/radius, OpenSky endpoints, polling/backoff, state quality/staleness, storage, replay, predictions, CPA, web display, runway/frequency/airport-reference data paths, runway model/validation/geometry, coverage analysis, approach, occupancy, replay UI, and risk settings. Risk settings include named profiles, separation/time thresholds, filtering and adjustment policies, confirmation and clear hysteresis, cooldown, data-loss timeout, and confidence thresholds. `AIR_COL_CONFIG` can point to an alternate YAML file. Keep OAuth secrets in environment variables.

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
