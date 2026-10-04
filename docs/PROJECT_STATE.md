# AIR_COL project state — Phase 10

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
│   ├── airport_activity.py
│   ├── activity_store.py
│   ├── enrichment.py
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
│   ├── weather.py
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
├── scripts/{coverage_report,diagnose_enrichment,diagnose_weather,evaluate_alerts,runway_stats,test_enrichment,verify_frequencies,verify_runways}.py
├── tests/{test_airport_activity,test_enrichment,test_events_api,test_airport_reference,test_alerts,test_config,test_cpa,test_coverage_analysis,test_enu_frame,test_event_store,test_evaluation,test_frequencies,test_geometry,test_opensky_limits,test_pairs,test_prediction,test_radio_aircraft_identity,test_replay,test_replay_clock_transport,test_risk,test_runways,test_state_manager,test_storage,test_weather,test_weather_decode}.py
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
- `GET /api/weather`: latest cached live AviationWeather.gov METAR and TAF for the configured ICAO airport. Each product has independent `status`, provider report (or `null`), error, fetched-at UTC and age; METAR includes observation time and TAF includes issue/validity times. Missing or failed products are explicitly unavailable/degraded; there is no weather fallback.
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

The bright browser layout uses a vertical side tab rail and light CARTO Voyager default with OSM/Dark alternatives. Phase 10.1 adds a Weather tab and header weather chip, selected-aircraft card, live lookup list fields, and replay transport controls. Browser visual behavior still needs a manual check on the user's Mac.

## Live weather and frequency display

`backend/airwatch/weather.py` polls AviationWeather.gov's Data API for METAR and TAF using the configured airport ICAO. METAR and TAF refresh every 600 s by default. Polling is backend-side because the provider API is not browser-CORS enabled. The service keeps the last fetched provider response on refresh failure but marks the product `DEGRADED`; report age beyond `weather.max_metar_age_s` is marked `STALE`. A provider no-report response or an empty valid response is `NOT_AVAILABLE`. It decodes wind, visibility, cloud layers, temperature/dewpoint, QNH and computed flight category. Runway wind components use true runway headings and assume METAR direction is true. No station data or weather value is inferred.

The header weather chip and Weather tab poll `/api/weather` and present raw METAR/TAF with provider time and retrieval age, decoded METAR fields, computed flight category and runway-relative wind components. It polls at the configured 600-second METAR interval. Radio frequencies come from the local `data/airport-frequencies.csv` via `GET /api/frequencies`; they are reference data, not a live radio receiver, and carry the official-AIP verification reminder.

Configuration is in `config.yaml` under `weather`: provider name, METAR/TAF URLs, refresh intervals, request timeout, maximum METAR age, and User-Agent. NOAA's usage notes set a request limit; this client polls each configured product only once per 10 minutes. `scripts/diagnose_weather.py` prints the configured request URL, actual HTTP status when reachable, raw JSON, decoded values and failure reason. Provider access may be blocked by local network or service conditions; the UI reports unavailable/degraded rather than estimating conditions.

## Data setup and verification before Phase 10

Local OurAirports inputs are `data/runways.csv`, `data/airport-frequencies.csv`, `data/airports.csv`, and `data/navaids.csv`. They are not tracked (`data/*.csv` is ignored); navaids is not used yet. Download manually from [OurAirports Data](https://ourairports.com/data/) and place under `data/`. Paths are in `runways`, `frequencies`, and `airport_reference` config sections.

OurAirports documents the runway-end coordinates as centers of the low/high ends and the displaced-threshold field as a length. We assume that displacement starts at the physical end and proceeds inward on the centerline. The computed 30-end landing threshold is offset 787 ft inward; runway-30 approach corridors, distance-to-threshold, and along-track values use that displaced point. This direction is a project geometry assumption that must be verified against the VOMM AIP/aerodrome chart. The [OurAirports runway field definitions](https://ourairports.com/help/data-dictionary.html) support the physical-end and displacement interpretation, but are not a replacement for the AIP.

The user supplied Chennai magnetic declination −1°4′ (−1.0666667°, west), based on WMM2025, on 2026-10-04. `runways.magnetic_variation_deg` is set to −1.0666667 and `magnetic_variation_confirmed` is true. Positive east remains positive by convention; true = magnetic + variation. Coordinate-derived magnetic headings now report as 69.9°, 249.9°, 118.5°, and 298.5° for 07, 25, 12, and 30, respectively. The VOMM designator comparison is within the configured 12° warning tolerance. Verify this city value against the effective official VOMM AIP/aerodrome chart before operational interpretation.

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


## Phase 10 work completed in this workspace

### Alert history and selection

`GET /api/events` now defaults to the active pipeline mode, accepts `mode=ALL`, and returns counts by mode plus an empty-result explanation. Status remains unfiltered by default; the dashboard uses the current mode and a 24-hour history range by default, with All/Last hour/Last 6 h/Last 24 h/custom options. Event time filters are Unix epoch seconds. The actual local SQLite check found one event at the time of implementation: one LIVE, RESOLVED, `research_default` event, with `first_seen_ts=1791128606.45493` and `resolved_ts=1791128697.83444`; these values are seconds. The screenshot's zero result was caused by requesting `mode=REPLAY` while the DB event was tagged LIVE. The API and dashboard now expose ALL and mode totals so this does not fail silently. Verify the current database, which may gain rows as the collector runs, with:

```bash
sqlite3 data/airwatch.db "SELECT mode,status,risk_profile,COUNT(*),MIN(first_seen_ts),MAX(first_seen_ts) FROM events GROUP BY mode,status,risk_profile;"
sqlite3 data/airwatch.db "SELECT event_id,mode,status,first_seen_ts,last_updated_ts,resolved_ts FROM events;"
curl 'http://127.0.0.1:8000/api/events'
curl 'http://127.0.0.1:8000/api/events?mode=LIVE'
curl 'http://127.0.0.1:8000/api/events?mode=REPLAY'
curl 'http://127.0.0.1:8000/api/events?mode=ALL'
```

The map deduplicates visible states by ICAO24, labels reduced-quality `!` markers in the legend, declutters labels, labels approach corridor and extended-centerline geometry, and caps runway centerline extensions at the config value `runways.centerline_extension_nm` (1 NM beyond each end). A shared selected ICAO24 is used across aircraft/approach/radio/activity/event lists; selecting pans to the observed or last observed coordinates and opens a map popup. A last-known selected aircraft gets a dashed marker at its last observed position and a no-longer-reporting message. Pair/event focus preserves both identifiers. Keyboard Escape closes selection and `/` focuses aircraft search. Side tab, map layers, labels and basemap are remembered in localStorage.

### Enrichment and radio estimates

`backend/airwatch/enrichment.py` asynchronously looks up adsbdb callsign and Mode S aircraft reference data through a selected-first, approach/departure-next background queue. In-memory TTL caches hold response payloads; `enrichment_cache` persists fetch time/source/negative status only. `data/airlines_override.yaml` is a small text reference and `data/airlines.dat` remains an optional OpenFlights fallback. The request-rate window and all TTLs live under `enrichment` in `config.yaml`. The map card hotlinks a provider photo when available and otherwise draws a local SVG silhouette. The selected map draws a dotted great-circle line to a reported or explicitly inferred VOMM destination; this is not a filed/actual path. `scripts/diagnose_enrichment.py` prints real request and parse details; live lookup results were not verified in this restricted network environment.

## Phase 10.1 updates and current limits

- Radio facility rows are expanded by facility. Each aircraft row carries its ICAO24 and selecting it selects that same aircraft; clicking the facility header highlights its full aircraft set without choosing one arbitrarily.
- Airport activity is generated from the shared LIVE and REPLAY pipeline and queried on a configured 15-second UI refresh. The current local DB contains one LIVE `LIKELY_LANDED` record; broader fade-out arrival/departure counts are not equivalent to approaches satisfying the configured sample, alignment and go-around rules.
- Event history defaults to the active mode, all statuses, and the last 24 hours. It returns matching counts and database mode counts. Custom time values are IST in the browser, converted to epoch seconds, constrained to earliest recorded time/current time, and validated server-side.
- Weather defaults to a 600-second refresh; `decode_metar` computes a flight category and runway wind components. `scripts/diagnose_weather.py` emits the real provider response when network access is available. NOAA was unreachable from the development environment during this phase, so no current weather values are claimed.
- Replay has pause/resume, speed, cycle step forward/back, timestamp seek and a separate replay summary rebuild endpoint. Seeking/back-step starts from the selected source cycle; it does not reconstruct earlier in-memory state history. Summary rebuild reads `raw_states` and writes only REPLAY-tagged derived summary rows, plus events/approach/activity produced by the shared REPLAY pipeline.
- `backend/airwatch/date_ranges.py` validates ordering, future times and range start against earliest recorded data. Browser `datetime-local` values are interpreted in Asia/Kolkata (IST).
- adsbdb aircraft and route payloads remain in an in-memory TTL cache only. Metadata in `enrichment_cache` does not contain provider response values. Photos are hotlinked, never downloaded. Source terms credit PlaneBase for aircraft data, David Taylor/Jim Mason for route data, and airport-data.com for photos; the public README does not specify a general API/photo license.
- Tests: `python -m pytest -q` and `npm --prefix frontend run build`; also `npm --prefix frontend run test:unit`. Real external lookups require a reachable provider; see README diagnostics.

adsbdb's README credits PlaneBase (aircraft data) and David Taylor and Jim Mason (flight-route data), and warns route data may not be incorporated into other databases without David Taylor's permission. No general API license statement was found in the public materials. AIR_COL therefore does not persist the route response. The `scripts/test_enrichment.py` checker reads the `raw_states` table through a read-only connection; it does append cache metadata to the main DB. A one-callsign check on this environment returned 0/1 airline, 0/1 route, and 0/1 aircraft-type matches. This is not a representative hit rate. Run with `--limit 25` on a networked Mac for a useful sample.

`GET /api/frequencies` continues to serve local OurAirports records. `GET /api/radio-estimates` classifies current aircraft into likely facilities using configured rules and returns aircraft counts. These values are estimates, not a radio observation. UI and README state that live ATC audio is not included; no audio source has been added.

### Inferred airport activity and approach data diagnosis

`airport_activity.py` produces only `LIKELY_LANDED` and `LIKELY_DEPARTED` research inferences and stores them in `airport_activity`, tagged LIVE/REPLAY. These records never enter pair, risk, alert, or occupancy calculations. Map markers use the last observed coordinates only and are visually dashed/labeled inferred; no position is invented. The airport activity panel and runway-end badges show inferred state. The configuration currently uses 5 NM / 1,500 ft landing limits, chosen around the previous recorded median last-seen arrival (2.88 NM, 746 ft above airport level) with margin for noisy public ADS-B. These are tuning parameters, not validated cutoffs. Runway occupancy still remains UNKNOWN/not assessable under the measured-coverage gate.

The current read-only runway-stat replay included 794 recorded cycles (2026-10-03T10:12:24Z–2026-10-04T17:04:45Z during this run). It detected 58 approach segments: 47 on RWY 07 and 11 on RWY 12; none on 25/30. Seventeen tracks had one distinct position timestamp (sample distribution min/median/max 1/2/6); position-time span min/median/max was 0/22/769 s. First distance-to-threshold min/median/max was 1.02/2.94/4.73 NM, and last distance was 0.15/1.95/3.25 NM. Historical inference counted 34 likely landings and 1 likely departure, not ground truth. Because the recording continued to grow between runs, the older 47 count (36 RWY07, 11 RWY12) and current 58 count are not a controlled before/after code comparison.

The prior report conflated 47 approach tracks with 47 corridor-participation segments below two samples: `runway_stats.py` had overwritten the approach-track count with the separate corridor segment count. This is corrected; the script now reports both. It also exports every approach's timestamps, distinct-position count, first/last threshold distance, age-compensated ENU position, along/cross-track values, implied glide angle, confidence and outcome to `approach_track_diagnostics.csv`. The current run counted 53 corridor segments with fewer than two unique position timestamps separately from 17 one-sample approach tracks. The current complete DB contains 796 coverage poll times at inspection, 41 gaps over 90 s, median cycle gap 30.47 s and maximum gap 34,174 s; no >90 s gap fell inside/adjacent to the 58 approach windows. Of 4,605 raw state rows, 3,596 distinct `(icao24, position_timestamp)` pairs were recorded and none had a missing position timestamp, so repeated timestamps exist but do not explain most sparse tracks. The best-supported explanation is sparse eligible reporting within short approach corridors plus the configured corridor/heading/speed/descent/freshness filters and ordinary receiver coverage limits. The data do not establish which aircraft were physically present when ADS-B reports stopped.

One genuine tracker issue was fixed: terminal runway-end tracks could be reused when the same aircraft reappeared much later. `approach.restart_after_s` (300 s) now starts a new segment after the prior terminal state; distinct `position_timestamp` values, not repeated poll sightings, determine sample count. Tracks with too few samples remain visible with LOW confidence. Do not loosen classification thresholds solely to increase counts without checking false matches against source tracks.

For a hand check, the current diagnostic includes IGO564M (ICAO24 `801410`) on runway end 12. It had 3 distinct position timestamps over 43 s, first/last range 2.61/0.22 NM, and its last age-compensated diagnostics were along-track 408.35 m, cross-track −11.96 m, implied glide angle 4.06°, heading difference 0.09°, and vertical rate −704.72 ft/min. Reproduce its underlying reports (epoch times are seconds) with:

```bash
sqlite3 data/airwatch.db "SELECT fetch_time,callsign,position_timestamp,latitude,longitude,baro_altitude_m,geo_altitude_m,track_deg,velocity_mps,vertical_rate_mps FROM raw_states WHERE lower(icao24)='801410' AND fetch_time BETWEEN 1791022567.636952 AND 1791022631.296587 ORDER BY fetch_time;"
.venv/bin/python scripts/verify_runways.py
.venv/bin/python scripts/runway_stats.py --db data/airwatch.db --output-dir reports/runway_stats
```

Use the runway-12 true heading and displaced threshold coordinates from the verification output; project the reports into the same airport ENU frame, age-compensate at the matching poll time, and apply `along_track_distance`, `cross_track_error`, and `atan2(height_above_threshold, along_track_distance)`. The stored raw rows show five polls but three distinct position timestamps for this segment, illustrating repeated timestamp de-duplication without interpolating an aircraft position.

### Phase 10 limitations and remaining verification

A live browser visual inspection was not available in this environment. The backend attempted startup but the sandbox denied local port binding; the live CLI exited cleanly with `DEGRADED` because `OPENSKY_CLIENT_ID`/`OPENSKY_CLIENT_SECRET` are not present here. The one-callsign adsbdb check cannot characterize provider coverage. Runway activity inference has no ground truth and must be checked against an independent public flight tracker. Current approach classifier does not give authoritative phase-of-flight labels. Occupancy remains unassessable (latest gate: about 5.67 h against 6 h minimum; 2.29 low-altitude reports/hour inside buffers versus 5 required; 0 on-ground reports/hour versus 5 required).

Replay implements pause/resume, cycle step forward/back, speed, and timestamp seek. `POST /api/replay/summaries/rebuild` separately runs the recorded range through a headless REPLAY collector and updates `replay_cycle_summaries`/`replay_summary_jobs`; it also exercises the shared replay event/approach/activity pipeline. A seek restarts at that source cycle, so state history before the chosen cycle is not reconstructed. Replay observations are never inserted into `raw_states`. Enrichment uses a prioritized queue for airborne LIVE aircraft; selected and background reference results remain in memory because of the adsbdb route-data restriction. The card may show Unknown/Route unavailable when provider/reference coverage is absent.

Latest automated verification for Phase 10.1: `.venv/bin/python -m pytest -q` passed 124 tests; `npm --prefix frontend run build` passed; `npm --prefix frontend run test:unit` passed one unit suite; compileall and `git diff --check` passed. `python -m backend.airwatch.cli --once` reports `DEGRADED`, aircraft=0, and no substitute data because credentials are absent in the environment.
