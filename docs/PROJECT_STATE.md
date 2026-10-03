# AIR_COL project state — Phase 7

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
│   ├── cli.py
│   ├── clock.py
│   ├── collector.py
│   ├── cpa.py
│   ├── evaluation.py
│   ├── event_store.py
│   ├── models.py
│   ├── opensky.py
│   ├── pairs.py
│   ├── prediction.py
│   ├── replay.py
│   ├── risk.py
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
├── scripts/evaluate_alerts.py
├── tests/{test_alerts,test_config,test_cpa,test_enu_frame,test_event_store,test_evaluation,test_opensky_limits,test_pairs,test_prediction,test_replay,test_risk,test_state_manager,test_storage}.py
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
- `GET /api/coverage`: recorded live coverage.
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

`config.yaml` holds airport, OpenSky, polling/backoff, state quality/staleness, storage, coverage, replay, prediction, CPA, web, and new `risk` settings. Risk includes active profile, level thresholds, alert age/lookahead limits, conservative unknown-vertical policy, filters, confirmation cycles/seconds, clear cycles/seconds, cooldown, data-loss timeout, confidence thresholds, and named profiles. It contains the comment that thresholds are research/demo values and not official ATC separation standards. Secrets remain environment-only. No dependency was added in Phase 7.

## Verification

```bash
.venv/bin/python -m pytest -q
npm --prefix frontend run build
.venv/bin/python -m backend.airwatch.cli --once
```

Tests include risk boundaries and adjustments, lifecycle/hysteresis/cooldown/data loss, event-store filters, and deterministic evaluation with an isolated temporary database. The API CLI remains live-only and never substitutes demo data.

## Known limitations

The risk profiles are configurable research examples, not validated separation criteria. The CPA itself assumes constant horizontal velocity and vertical rate and inherits public ADS-B latency, missing reports, and position error. Candidate confirmation adds an additional poll interval. A risk result and any alert are not an operational warning and must not be used for ATC, collision avoidance, or runway decisions. A long busy replay or evaluation can produce many event updates; SQLite writes are asynchronous through a worker thread, while event history is retained.
