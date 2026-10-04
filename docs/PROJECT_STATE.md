# AIR_COL project state — Phase 9

AIR_COL is a research and education prototype for live ADS-B around one configured airport. It is **not ATC, TCAS/ACAS, or a certified safety system**. It uses live OpenSky observations and app-recorded observations for replay and offline evaluation. It does not generate fallback or demo aircraft. Potential Aircraft Conflict entries are research estimates, not official separation determinations.

## Folder tree

```text
AIR_COL/
├── .env.example
├── .gitignore
├── config.yaml
├── requirements.txt
├── backend/airwatch/
│   ├── api/{__init__.py,app.py}
│   ├── alerts.py
│   ├── approach.py
│   ├── approach_store.py
│   ├── cli.py
│   ├── clock.py
│   ├── collector.py
│   ├── cpa.py
│   ├── coverage_analysis.py
│   ├── evaluation.py
│   ├── event_store.py
│   ├── geometry.py
│   ├── models.py
│   ├── opensky.py
│   ├── occupancy.py
│   ├── pairs.py
│   ├── prediction.py
│   ├── replay.py
│   ├── risk.py
│   ├── runways.py
│   ├── state_manager.py
│   └── storage.py
├── frontend/
│   ├── index.html
│   ├── package.json
│   ├── package-lock.json
│   └── src/
│       ├── components/{AlertsPanel,BottomPanel,ClosestApproachesTable,DashboardHeader,DetailsPanel,FiltersPanel,MapView,ReplayControls}.jsx
│       ├── styles/tokens.css
│       ├── config.js
│       ├── main.jsx
│       └── style.css
├── scripts/{coverage_report,evaluate_alerts,runway_stats,verify_runways}.py
├── tests/{test_alerts,test_config,test_cpa,test_coverage_analysis,test_enu_frame,test_event_store,test_evaluation,test_geometry,test_opensky_limits,test_pairs,test_prediction,test_replay,test_risk,test_runways,test_state_manager,test_storage}.py
└── docs/PROJECT_STATE.md
```

## Install and run (macOS)

From `/Users/joelissac/Documents/ChatGPT/AIR_COL`:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
export OPENSKY_CLIENT_ID='your-client-id'
export OPENSKY_CLIENT_SECRET='your-client-secret'
python -m backend.airwatch.cli --once
uvicorn backend.airwatch.api.app:app --reload --reload-dir backend
```

In another terminal:

```bash
cd /Users/joelissac/Documents/ChatGPT/AIR_COL/frontend
npm install
npm run dev
```

Open Vite's printed URL, normally `http://localhost:5173`. Create OAuth credentials through the OpenSky account portal and keep them in environment variables. Missing credentials, HTTP errors, rate limits, and empty feeds stay degraded/no-data; no aircraft are synthesized.

To select the test profile for the backend, set it before starting the API:

```bash
export AIR_COL_RISK_PROFILE=sensitive_test
uvicorn backend.airwatch.api.app:app --reload --reload-dir backend
```

Omit that environment variable to use `risk.active_profile` from `config.yaml` (`research_default`). The test profile is deliberately loose, is visibly identified in the dashboard, and its events are tagged with the profile name.

## Alert timing

The default collector poll interval is 30 seconds. Confirmation requires two qualifying poll cycles **and** at least 30 seconds of pipeline-clock time. With evenly spaced polls, a condition first sampled on a cycle can become active on the next qualifying cycle, about 30 seconds later; depending on when the condition arose relative to the poll, sampling adds up to nearly another interval. Public ADS-B reports may already be 10–30 seconds old, so the visible alert may lag the underlying situation further. The confirmation elapsed time is stored with each event. Startup warns if a configured age threshold is not greater than the poll interval; confidence thresholds may intentionally fall below it.

## API and WebSocket contracts

- `GET /api/health`: `{mode,status,updated_at,message,risk_profile}`.
- `GET /api/airport`: airport/radius, map settings, replay/prediction settings, and display-only CPA breakpoints.
- `GET /api/aircraft`: active LIVE or REPLAY state snapshot.
- `GET /api/predictions` and `GET /api/aircraft/{icao24}/prediction`: trajectory results and explicit prediction skip reasons.
- `GET /api/pairs`: closest-approach research estimates and pair-filter counts.
- `GET /api/risk/config`: active profile, profile thresholds, alert timing, filters, and null-vertical policy.
- `GET /api/alerts/active?mode=LIVE|REPLAY`: current candidate/active/escalated entries. When omitted, mode follows the selected pipeline.
- `GET /api/events`: paginated event history. Filters are `status`, `risk`, `mode`, `aircraft` (ICAO24), `start`, `end` (UTC epoch seconds), `limit`, and `offset`. Mode defaults to LIVE; query REPLAY explicitly to see replay events.
- `GET /api/events/{event_id}`: full stored event with snapshots, score components, reasons, and CPA coordinates.
- `GET /api/coverage`: recent recorded live coverage.
- `GET /api/runways`: runway metadata plus core, buffer, extended centerline and approach corridor GeoJSON in standard `[longitude, latitude]` coordinate order. Returns HTTP 503 with manual file instructions if runway data is missing or contains no matching airport rows.
- `GET /api/coverage/runway-summary`: headline numbers from `coverage_report.output_dir/coverage_summary.json`, HTTP 404 until the report script has generated one.
- Replay: `GET /api/replay/status`, `POST /api/replay/start` with optional `{start_time,end_time,speed}`, and `POST /api/replay/stop`.
- `/ws/live`: existing snapshot envelope `{type,mode,ts,source_status,data}`. Alert messages use `type: "alert"`, the same outer envelope, and `data: {sub_event,event}` where `sub_event` is `opened`, `updated`, `escalated`, or `resolved`.

Risk is computed from Phase 6 pair records. The engine reports `level`, `score_components`, human-readable `reasons`, confidence, and `filtered_reason`. By default it ignores both-on-ground pairs, diverging pairs, pairs beyond lookahead, and data too old for alerting. Unknown vertical separation follows the configured conservative policy. Uncertainty adjusts horizontal separation by the selected profile multiplier. Optional closing-speed, poor-data, and in-trail adjustments are included in the explanation.

## Lifecycle and persistence

`backend/airwatch/alerts.py` tracks canonical `(event_type,pair_key)` keys and clock timestamps. A pair advances CANDIDATE → ACTIVE → ESCALATED → RESOLVED; below-threshold hysteresis prevents flicker, and cooldown recurrences continue the same event. A missing/stale aircraft resolves as `data_lost`, never as safe. A replay boundary is recorded as `replay_ended` or `replay_stopped`. Managers are separate by LIVE/REPLAY and profile, and every stored event has `mode` and `risk_profile`.

SQLite `events` is created alongside `raw_states` and `coverage_samples`. It stores lifecycle state, current/peak risk, latest and minimum separations, CPA times and coordinates, confirmation timing, confidence, reasons/score components, and first/peak aircraft snapshots. Indexes cover mode/status, risk, pair/time, aircraft/time, and resolved time. Events persist across backend restarts. Queries default to LIVE so replay events are not mixed into live history.

## Dashboard

The Alerts tab shows the active risk profile, the `sensitive_test` notice, active candidates/alerts, risk legend, and filterable event history. Active alerts draw risk-colored pulsing rings on both aircraft, current-position and predicted-CPA lines, and a labeled CPA point. Selecting an event focuses the map; selecting history shows its full JSON in the details panel. Notifications are visual only. Callsign labels are decluttered near the airport and at lower zoom, and prediction time labels only show for the selected aircraft. Predicted paths use a stronger blue on the light basemap.

## Offline evaluation

Evaluation reads only app-recorded poll cycles, drives the same state manager, pair calculations, risk engine, alert lifecycle, and replay clock, and does not write to the live database:

```bash
.venv/bin/python scripts/evaluate_alerts.py \
  --start 2026-10-03T10:00:00Z --end 2026-10-03T11:00:00Z \
  --profile research_default --db data/airwatch.db --output-dir reports/alert_evaluation
```

Arguments `--start` and `--end` require ISO timestamps with `Z` or a UTC offset. The script writes `alert_events.csv` and `alert_evaluation.md`: poll cycles, aircraft-hours, evaluated pairs, alerts/hour by peak risk, duration stats, data-loss resolutions, low-confidence share, and closest pairs. Compare profiles on the same range and tune one threshold at a time. For normal traffic, the goal is a low false-alert rate, not finding conflicts. The intentionally loose `sensitive_test` profile is for exercising the UI and lifecycle with recorded real traffic, not for research conclusions.

## Configuration

`config.yaml` holds airport, OpenSky, polling/backoff, state quality/staleness, storage, coverage, replay, prediction, CPA, web, risk, runway and coverage-report settings. Runway values include the CSV/override paths, closed-runway policy, default width, magnetic variation, validation tolerances, buffer expansion, corridor dimensions, and ceiling. Coverage settings include the above-airport altitude bands, airport-distance rings, poll/track gap definitions, minimum sample counts, and recommendation criteria. No dependency was added in Phase 8; Shapely and pyproj were already present.

## Phase 8 runway model and geometry

`backend/airwatch/runways.py` parses OurAirports `runways.csv` locally; the service never downloads data. Put the manually downloaded file at `data/runways.csv`. Optional `data/runways_override.yaml` corrections are logged when applied. The loader rejects a missing or malformed dataset with a specific explanation, filters closed surfaces according to config, falls back to a configured width when the source width is blank, and emits validation warnings for heading/designator+variation, threshold distance from airport reference, and source length versus coordinate length. Headings are computed geodetically from threshold coordinates. Designator headings are magnetic; configured positive-east variation uses true = magnetic + variation. A zero variation remains a warning until the current value for the data/chart effective date is checked.

`backend/airwatch/geometry.py` contains pure ENU/Shapely helpers. Runway core polygons use endpoint coordinates and width; buffered polygons expand laterally and longitudinally. Each end has a displaced-threshold-aware approach trapezoid extending outward on reciprocal runway heading. The runway centerline extends on both ends by configured approach length. API geometry is transformed back into standard GeoJSON longitude/latitude. No aircraft/runway classification or runway alerts are present in this phase.

Verification and coverage commands:

```bash
# Save the manual download to data/runways.csv first
.venv/bin/python scripts/verify_runways.py
.venv/bin/python scripts/coverage_report.py \
  --db data/airwatch.db --output-dir reports/coverage
```

The verification output prints computed true and magnetic headings plus magnetic designator headings and reminds the operator to compare with official AIP/aerodrome charts. `coverage_report.py` opens the SQLite database read-only and analyzes only recorded source data. It writes report Markdown, a compact JSON summary for the API, and CSVs for altitude bands, distance rings, hourly counts, update intervals, fade-out tracks/histograms, buffer hits, and corridor hits. Its arrival/departure classifications are simple heuristics from descending/climbing and moving closer/farther; they describe last/first received reports, not actual disappearance. With no runway CSV, non-runway statistics still run and runway-dependent measures are explicitly unavailable. A recording under configured minimum duration is labeled insufficient data.

## Phase 8 current input/report state

At the final Phase 8 verification, `data/runways.csv` and `data/runways_override.yaml` were absent. The live collector continued recording while the report ran. The latest report contained 1,855 raw-state rows, 362 poll cycles, 59 distinct aircraft over 3.32 hours, with 11 poll gaps above the configured 90 seconds. It showed 213 reports in the 0–500 ft band, 163 in 500–1,000 ft, no received on-ground reports, and median data age about 6.66 s. The data is below the configured six-hour minimum; runway geometry was unavailable, so runway-specific recommendations are **insufficient data**. Counts can increase while collection remains active. These values describe received reports only, not complete receiver coverage.

## Verification

```bash
.venv/bin/python -m pytest -q
npm --prefix frontend run build
.venv/bin/python -m backend.airwatch.cli --once
```

Tests include risk boundaries/adjustments, lifecycle/hysteresis/cooldown/data loss, event-store filters, deterministic evaluation, runway data loading/validation, ENU geometry edge cases, and read-only coverage analysis on temporary databases. Test inputs are isolated to tests and are not used by the running application. The API and CLI remain live-only and never substitute demo aircraft.

## Known limitations

The risk profiles are configurable research examples, not validated separation criteria. The CPA itself assumes constant horizontal velocity and vertical rate and inherits public ADS-B latency, missing reports, and position error. Candidate confirmation adds an additional poll interval. Runway records and magnetic variation must be checked against the current official AIP/aerodrome charts; the validation checks are warnings only. Public ADS-B generally cannot establish reliable ground/runway occupancy because low-altitude and surface coverage can be poor. Coverage report movement-direction classes are heuristics, not flight phase labels from an authoritative source. Alerts and runway geometry are not operational data and must not be used for ATC, separation, collision avoidance, or runway decisions.

## Phase 9: approach tracking and experimental occupancy

`backend/airwatch/approach.py` consumes the manager's age-compensated `analysis_x_m` / `analysis_y_m` fields and timestamped ENU history. Those existing names were retained. True track and runway heading are compared using the Phase 8 wrap-safe `heading_difference`; runway true headings are calculated from threshold coordinates. The tracker is called from the collector's existing Clock-driven pipeline callback for both LIVE and REPLAY. It adds candidate/confirmation/hysteresis outcomes, glide-angle and heading evidence, confidence, and min–max ETA windows. `approach_tracks` rows are tagged LIVE/REPLAY; replay observations still never enter `raw_states`.

`GET /api/approaches` exposes current classifications and persisted approach tracks. `GET /api/runway-status` exposes per-end approach counts and runway-use estimate plus experimental runway occupancy statuses. Occupancy has a hard coverage gate and ghost retention; it never reports “clear.” `GET /api/replay/sessions` and `GET /api/replay/cycles` list source-recorded sessions/cycle counts read-only. Cycle-level pair and alert values are unavailable where they were not recorded as per-cycle derived data; the UI displays these as unavailable.

New config sections: `approach` (geometry/quality limits, confirmation, hysteresis, speed uncertainty, runway-in-use evidence), `occupancy` (persistence, ghost TTL, evidence and coverage gate), and `replay_ui.session_gap_s`. Occupancy coverage uses **runway-buffer-specific** low-altitude and on-ground rates. The Phase 8 report has only airport-wide low-altitude rates; those are intentionally not accepted as a runway-buffer gate.

The updated coverage run spans 29.47 elapsed hours but only 4.27 hours of observed poll-to-poll coverage (the report now excludes gaps over the configured 90 seconds from its recording-hours gate). It includes 582 recorded poll cycles / 3,042 reports, with 41 gaps longer than 90 seconds and zero on-ground reports. `data/runways.csv` is absent, so runway-buffer rates, approach corridors, and runway geometry are unavailable; the report says approach monitoring and occupancy are insufficient data. `/api/runway-status` therefore returns `occupancy_assessable: false` and `UNKNOWN` when runway rows are available, with the explicit message “Runway occupancy not assessable with current data.” These figures are received data only, not proof of no activity.

Run `scripts/runway_stats.py --db data/airwatch.db --output-dir reports/runway_stats` to replay the selected recorded range through the state and approach trackers. The script writes JSON, a per-hour CSV, and Markdown. Install/run commands remain as above; API and UI launch as documented. Tests now include approach decision math/tracking, approach SQLite mode separation, and occupancy unknown/single-sample/ghost behavior.

The Phase 9 browser redesign uses a bright theme, a vertical side tab rail, a scrolling panel, Overview/Aircraft/Approaches/Alerts/Replay views, and light CARTO Voyager default with OSM/Dark alternatives. Some requested replay conveniences remain limited: replay can start from a selected timestamp, but there is no true pause/step/scrub transport yet, and cycle table pairs/opened-alert counts are explicitly unavailable. Validate sizing and map tile availability in the browser.
