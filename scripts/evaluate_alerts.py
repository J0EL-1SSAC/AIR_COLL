#!/usr/bin/env python3
"""Evaluate alert thresholds using only AIR_COL-recorded ADS-B observations."""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.airwatch.evaluation import evaluate_recorded_range, write_evaluation_report


def utc_epoch(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamps must include a UTC offset or Z")
    return parsed.astimezone(timezone.utc).timestamp()


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate AIR_COL alerts over recorded real ADS-B data.")
    parser.add_argument("--start", required=True, type=utc_epoch, help="UTC ISO timestamp (for example 2026-10-03T10:00:00Z)")
    parser.add_argument("--end", required=True, type=utc_epoch, help="UTC ISO timestamp (for example 2026-10-03T11:00:00Z)")
    parser.add_argument("--profile", help="risk profile; defaults to config.yaml active_profile")
    parser.add_argument("--db", type=Path, help="SQLite database; defaults to config.yaml storage.database_path")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports" / "alert_evaluation")
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--start must be before --end")
    with (ROOT / "config.yaml").open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    profile = args.profile or config["risk"]["active_profile"]
    db_path = args.db or ROOT / config["storage"]["database_path"]
    report = asyncio.run(evaluate_recorded_range(db_path=db_path, start=args.start, end=args.end,
                                                 config=config, profile=profile))
    csv_path, md_path = write_evaluation_report(report, args.output_dir)
    print(f"Mode: {report['mode']} | Profile: {report['profile']}")
    print(f"Poll cycles: {report['poll_cycles']} | Aircraft-hours: {report['aircraft_hours']:.3f} | Pairs evaluated: {report['pairs_evaluated']}")
    print("Alerts/hour by peak level: " + ", ".join(f"{level}={value:.3f}" for level, value in report["alerts_opened_per_hour_by_peak_risk"].items()))
    print(f"Median/max duration (s): {report['median_alert_duration_s']} / {report['max_alert_duration_s']}")
    print(f"Ended with data_lost: {report['data_lost_events']} | LOW confidence share: {report['low_confidence_share']:.1%}")
    print(f"Wrote: {csv_path}\nWrote: {md_path}")
    if report["poll_cycles"] == 0:
        print("No recorded OpenSky poll cycles exist in that UTC range; no data was synthesized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
