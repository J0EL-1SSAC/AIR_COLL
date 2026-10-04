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
│   ├── airport_reference.py
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
│   ├── frequencies.py
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
├── scripts/{coverage_report,evaluate_alerts,runway_stats,verify_frequencies,verify_runways}.py
├── tests/{test_airport_reference,test_alerts,test_config,test_cpa,test_coverage_analysis,test_enu_frame,test_event_store,test_evaluation,test_frequencies,test_geometry,test_opensky_limits,test_pairs,test_prediction,test_replay,test_risk,test_runways,test_state_manager,test_storage}.py
├── data/ (local OurAirports CSVs and SQLite files; ignored by Git)
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
- `GET /api/frequencies`: selected local frequency rows plus clearly labeled estimated DEP/CLR facility fallback and AIP verification notices.
- `GET /api/reference/airports/{code}`: offline ICAO/IATA/OurAirports-ident lookup, backed by lazily built indexes; only one matching row is returned.
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

This section's original Phase 8 values are historical. Current local data setup and refreshed coverage results are documented in “Data setup and verification before Phase 10” below.

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

The earlier Phase 9 recording values are historical; see the data verification section appended below for the current report. `/api/runway-status` returns `occupancy_assessable: false` and `UNKNOWN` when the measured gate fails, with the explicit message “Runway occupancy not assessable with current data.” These figures are received data only, not proof of no activity.

Run `scripts/runway_stats.py --db data/airwatch.db --output-dir reports/runway_stats` to replay the selected recorded range through the state and approach trackers. The script writes JSON, a per-hour CSV, and Markdown. Install/run commands remain as above; API and UI launch as documented. Tests now include approach decision math/tracking, approach SQLite mode separation, and occupancy unknown/single-sample/ghost behavior.

The Phase 9 browser redesign uses a bright theme, a vertical side tab rail, a scrolling panel, Overview/Aircraft/Approaches/Alerts/Replay views, and light CARTO Voyager default with OSM/Dark alternatives. Some requested replay conveniences remain limited: replay can start from a selected timestamp, but there is no true pause/step/scrub transport yet, and cycle table pairs/opened-alert counts are explicitly unavailable. Validate sizing and map tile availability in the browser.

## Data setup and verification before Phase 10

Local OurAirports inputs are `data/runways.csv`, `data/airport-frequencies.csv`, `data/airports.csv`, and `data/navaids.csv`. They are not tracked (`data/*.csv` is ignored); navaids is not used yet. Download manually from [OurAirports Data](https://ourairports.com/data/) and place under `data/`. Paths are in `runways`, `frequencies`, and `airport_reference` config sections.

OurAirports documents the runway-end coordinates as centers of the low/high ends and the displaced-threshold field as a length. We assume that displacement starts at the physical end and proceeds inward on the centerline. The computed 30-end landing threshold is offset 787 ft inward; runway-30 approach corridors, distance-to-threshold, and along-track values use that displaced point. This direction is a project geometry assumption that must be verified against the VOMM AIP/aerodrome chart. The [OurAirports runway field definitions](https://ourairports.com/help/data-dictionary.html) support the physical-end and displacement interpretation, but are not a replacement for the AIP.

Magnetic variation remains unconfirmed. `runways.magnetic_variation_confirmed` is `false`; the numeric 0.0 is ignored as a placeholder, magnetic heading is printed `UNCONFIRMED`, and warnings remain active. Do not set this true until the user supplies the value for the relevant chart date.

`backend/airwatch/frequencies.py` loads selected local frequency rows and logs each override from `data/frequencies_override.yaml`. The file corrects `SCHENNAI RADARS` to `CHENNAI RADARS`. Since no DEP or CLR entries exist, APP is shown only as an explicitly labeled facility estimate for those roles, with AIP verification required. `scripts/verify_frequencies.py` prints the VOMM records; `GET /api/frequencies` exposes them. `backend/airwatch/airport_reference.py` lazily builds an indexed SQLite database at `data/airport_reference.db`; `GET /api/reference/airports/{code}` resolves ICAO, IATA, and OurAirports ident locally and returns only one record. The frontend does not load the full 86k-row reference CSV.

Commands:

```bash
.venv/bin/python scripts/verify_runways.py
.venv/bin/python scripts/verify_frequencies.py
.venv/bin/python scripts/coverage_report.py --db data/airwatch.db --output-dir reports/coverage
.venv/bin/python scripts/runway_stats.py --db data/airwatch.db --output-dir reports/runway_stats
.venv/bin/python -m pytest -q
```

Local runway verification loaded two VOMM runway records: 07/25 (12,001 × 148 ft, ASP) and 12/30 (6,708 × 148 ft, PEM). Coordinate-derived true headings: 68.9°, 248.9°, 117.4°, 297.4°. OurAirports `heading_degT` fields: 69°, 249°, 117.6°, 297.6°. The 30 end has 787 ft displacement. Surface `PEM` is retained as the source code and is not reinterpreted. **Verify all identifiers, threshold coordinates/displacement, dimensions, surface, true and magnetic headings against the official AIP/aerodrome chart.**

The two physical runway centerlines intersect at ENU (1028.9 m E, 41.8 m N), 467.3 m from the 25 landing threshold and 498.8 m from the displaced 30 landing threshold. The 25/30 buffers overlap by 36,367.2 m², and their approach corridors overlap by 1,671,231.3 m². Their true headings differ by 48.6°, while each configured ±15° heading acceptance window is disjoint, so one true track cannot pass both heading gates. The 07/12 approach corridors have no intersection; their heading windows are also disjoint by the same 48.6°. Corridor overlap for 25/30 is expected from the crossing geometry, but heading gating keeps the runway-end classifier separable. These geometries still need chart review.

The latest coverage report used 3,650 received reports, 660 poll cycles, 156 distinct aircraft, and 4.81 observed hours, with 41 poll gaps longer than the configured threshold. Heuristic fade-out classified 71 arrivals, 51 departures, and 20 ambiguous tracks. Arrival last-seen median was 746 ft above airport level and 2.88 NM from the nearest threshold. Arrival altitude counts were 18 / 34 / 6 / 1 / 4 / 8 in the configured bands from 0–500 ft through above 5,000 ft; threshold-distance counts were 25 / 30 / 5 / 7 / 4 in 0–2 / 2–5 / 5–10 / 10–20 / 20–40 NM. These are last received reports, not aircraft disappearance points.

The report found 11 runway-buffer reports (8 for 07/25; 3 for 12/30), all under 1,000 ft above airport level. Approach-corridor reports below ceiling: 07 601, 25 60, 12 197, 30 21. The runway-stat replay processed 660 cycles and reported 47 approach estimates (36 end 07, 11 end 12, none for 25/30) and 47 corridor segments with fewer than two distinct position samples; per-hour and per-track details are in `reports/runway_stats/`. It counted 0 on-ground aircraft. Occupancy is **not assessable**: 4.81 observed hours vs 6 required; runway-buffer low-altitude 2.29 reports/hour vs 5 required; on-ground 0/hour vs 5 required. All replayed runway statuses were `UNKNOWN`, and the coverage recommendation remains **insufficient data**. Zero received on-ground reports does not mean the runways were empty; all values describe only received data.

The coverage renderer had an uncaught `NameError` when formatting its final runway-buffer interpretation; that was corrected. The VOMM reference-data test reads the manually supplied local CSV and skips when another checkout lacks it; compact synthetic airport/frequency CSVs in their tests are isolated fixtures only. The full suite currently passes 98 tests.
