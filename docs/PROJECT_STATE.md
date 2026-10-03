# AIR_COL project state — Phase 6

Research and education prototype for live ADS-B around one configured airport. This is **not ATC, TCAS/ACAS, or a certified safety system**. It uses live OpenSky observations, with explicit degraded/no-data handling, and replays only observations recorded by this application. Potential closest-approach values are research estimates; they do not establish a conflict or official separation.

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
│   ├── cpa.py
│   ├── models.py
│   ├── opensky.py
│   ├── pairs.py
│   ├── prediction.py
│   ├── replay.py
│   ├── state_manager.py
│   └── storage.py
├── frontend/
│   ├── index.html
│   ├── package.json
│   ├── package-lock.json
│   └── src/
│       ├── components/{BottomPanel,ClosestApproachesTable,DashboardHeader,DetailsPanel,FiltersPanel,MapView,ReplayControls}.jsx
│       ├── styles/tokens.css
│       ├── config.js
│       ├── main.jsx
│       └── style.css
├── tests/{test_config,test_cpa,test_enu_frame,test_opensky_limits,test_pairs,test_prediction,test_replay,test_state_manager,test_storage}.py
└── docs/PROJECT_STATE.md
```

## Install and run (macOS)

From the root `/Users/joelissac/Documents/ChatGPT/AIR_COL`:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
export OPENSKY_CLIENT_ID='your-client-id'
export OPENSKY_CLIENT_SECRET='your-client-secret'
python -m backend.airwatch.cli --once
uvicorn backend.airwatch.api.app:app --reload --reload-dir backend
```

In a second terminal:

```bash
cd /Users/joelissac/Documents/ChatGPT/AIR_COL/frontend
npm install
npm run dev
```

Open the URL Vite prints (normally `http://localhost:5173`). Use OpenSky's account portal to create OAuth2 client credentials. Keep them in environment variables; never put secrets in `config.yaml` or commit them. The map tile basemaps require internet access. Review CARTO and OpenStreetMap tile usage terms before sustained use.

The command `python -m backend.airwatch.cli --once` remains a live-only one-poll check. Missing credentials, API errors, and rate limits produce degraded/no-data output; the app does not create substitute aircraft.

## API and WebSocket contracts

- `GET /api/health`: `{mode, status, updated_at, message}`; mode is `LIVE` or `REPLAY`.
- `GET /api/airport`: configured airport/radius, map settings, replay settings, altitude bands, and display-only CPA separation thresholds.
- `GET /api/aircraft`: active selected-pipeline snapshot and mode.
- `GET /api/predictions`: batch constant-velocity trajectories or explicit skip reason codes, plus mode/model/horizons.
- `GET /api/aircraft/{icao24}/prediction`: one active aircraft prediction; 404 if inactive.
- `GET /api/pairs`: `{mode, updated_at, pairs_considered, pairs_returned, filtered_by, pairs}`. Each pair has a canonical lowercase `icao24|icao24` key, two aircraft identities, current and CPA horizontal/vertical separation, raw and clamped CPA time, closing speed, convergence/past flags, common altitude basis (or unavailable reason), data age, combined uncertainty, and each aircraft's and midpoint's CPA ENU and WGS84 coordinates. `filtered_by` contains first-rejection counts for the configured filters and spatial candidate reduction. This is a research/debug endpoint, not an alert or conflict determination.
- `GET /api/coverage`: recorded live-feed coverage samples.
- Replay controls: `GET /api/replay/status`, `POST /api/replay/start` with optional `{start_time,end_time,speed}` (Unix seconds), and `POST /api/replay/stop`.
- `/ws/live`: `{type, mode, ts, source_status, data}`. CPA and predictions are REST requests to keep WebSocket snapshots compact.

## CPA calculation and performance

`backend/airwatch/cpa.py` contains pure ENU CPA math. For relative position `r = B − A` and velocity `v = vB − vA`, it reports `t_raw = −(r·v)/|v|²`, clamped to `[0, lookahead]` for the displayed CPA. Near-zero relative speed and position use configured epsilons. Closing speed is signed (positive while closing); `cpa_in_past` flags a negative raw time. Vertical separation is projected using both vertical rates at clamped CPA time. Both aircraft use the same geometric altitude when available for both, otherwise the same barometric altitude; mixed bases produce unavailable vertical separation.

`backend/airwatch/pairs.py` uses same/adjacent cells in an ENU uniform grid before pair CPA calculations. It filters airborne, geofenced, fresh, sufficiently good tracks, current distance, altitude band, lookahead and CPA distance. This removes distant pair calculations for a typical distributed airport-area feed. With ordinary occupancy, a few hundred tracks should be comfortable; a densely packed single grid neighborhood still has quadratic candidate work, and no hard benchmark bound is claimed. Results use Phase 3 age-compensated ENU state, the active injected clock, and Phase 5 uncertainty model, so repeat replay runs over the same recorded range are deterministic.

## Frontend behavior

The React + Vite + Leaflet dashboard uses a dark CARTO basemap by default, with an OSM light switch and a notice/fallback when dark tiles fail. Trails, predictions, uncertainty circles, closest-approach lines, and the radius ring are independently selectable; trails and predictions are enabled by default. Airport and aircraft symbols are custom SVG markers. The dashboard includes left layer/filter and right detail panels, a compact responsive header, closest-approaches table, replay controls, alert placeholder for Phase 7, a legend, and the persistent research disclaimer. Side panels collapse, and clicking a closest-approach row fits the map to both aircraft's CPA positions.

## Configuration

`config.yaml` contains airport identity/coordinates/radius; OpenSky OAuth/API endpoints, timeout, and quota; polling/backoff; state history/quality/staleness; storage path/batching; coverage; replay rate; prediction model/horizons/skip rules/ground elevation/uncertainty; and CORS/web display settings. Phase 6 adds `cpa.lookahead_s`, `horizontal_cutoff_nm`, `altitude_band_ft`, numeric epsilons, `altitude_basis_policy`, `minimum_data_quality`, `uncertainty_combination`, and display-only `display_near_nm` / `display_amber_nm`. OpenSky credentials remain environment-only.

## Database

`data/airwatch.db` is SQLite with `raw_states` and `coverage_samples`. `raw_states` stores live OpenSky reports by fetch cycle; `coverage_samples` stores poll coverage summaries, including successful empty polls. Replay reads recorded rows and does not record the replayed observations again. Pair results and predictions are computed from the selected in-memory pipeline and are not persisted. The database and SQLite sidecars are ignored by Git.

## Tests and build

```bash
python -m pytest
npm --prefix frontend run build
```

Tests cover config, ENU conversion, OpenSky limits, state management, storage/replay, trajectory math, CPA edge cases, altitude-source consistency, pair filtering, and canonical pair records.

## Known limitations

CPA assumes constant horizontal velocity and vertical rate over the lookahead; it does not model turns, wind, intent, or calibrated uncertainty. The position uncertainty combines per-aircraft uncertainty using the configured rule and is not a certified error bound. ADS-B reports can be delayed, missing, or inaccurate, especially at low altitude and on the ground. CPA display separation bands are visual research cues, not risk classifications. The app must not be used for operational separation or runway-safety decisions.
