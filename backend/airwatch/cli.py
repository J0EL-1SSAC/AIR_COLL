from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import yaml

from .collector import LiveCollector
from .models import AirportCenter
from .opensky import OpenSkySource
from .storage import SQLiteRecorder

_M_TO_FT = 3.280839895


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    airport = config["airport"]
    if not (-90 <= airport["latitude"] <= 90 and -180 <= airport["longitude"] <= 180):
        raise ValueError("Airport latitude/longitude are invalid")
    if config["airport"]["radius_nm"] <= 0:
        raise ValueError("airport.radius_nm must be positive")
    return config


async def run(config: dict, once: bool) -> int:
    a, o, c = config["airport"], config["opensky"], config["collector"]
    storage, manager = config["storage"], config["state_manager"]
    db_path = Path(storage["database_path"])
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    recorder = SQLiteRecorder(db_path, batch_size=int(storage["batch_size"]),
                              flush_interval_s=float(storage["flush_interval_s"]),
                              low_altitude_m=float(c["low_altitude_ft"]) / _M_TO_FT,
                              low_quality_after_s=float(manager["low_quality_after_s"]))
    await recorder.start()
    source = OpenSkySource(token_url=o["token_url"], api_url=o["api_url"], timeout_s=float(o["request_timeout_s"]))
    collector = LiveCollector(source=source, center=AirportCenter(a["icao"], float(a["latitude"]), float(a["longitude"])),
                              radius_nm=float(a["radius_nm"]), poll_interval_s=float(c["poll_interval_s"]),
                              max_backoff_s=float(c["max_backoff_s"]),
                              sparse_count_threshold=int(c["sparse_count_threshold"]),
                              low_altitude_ft=float(c["low_altitude_ft"]),
                              manager_config=manager, recorder=recorder,
                              daily_credit_quota=float(o["daily_credit_quota"]),
                              estimated_credits_per_request=float(o["estimated_credits_per_states_request"]))
    try:
        if not once:
            await collector.run_forever()
            return 0
        success = await collector.poll_once()
        print(f"{collector.updated_at} | {collector.center.icao} | {collector.status} | aircraft={collector.aircraft_count} | low/ground={collector.low_or_ground_count}")
        print(collector.message)
        if not collector.latest:
            print("NO DATA: no live aircraft positions available; no substitute data used.")
        print(f"{'ICAO24':8} {'CALLSIGN':10} {'ALT ft':>8} {'SPEED kt':>9} {'HDG°':>6} {'GROUND':>7} {'AGE s':>7}")
        for aircraft in collector.latest:
            altitude = aircraft["altitude_ft"]
            speed = aircraft["speed_kt"]
            print(f"{aircraft['icao24']:8} {(aircraft['callsign'] or '—')[:10]:10} {('—' if altitude is None else f'{altitude:.0f}'):>8} {('—' if speed is None else f'{speed:.0f}'):>9} {('—' if aircraft['track_deg'] is None else f'{aircraft['track_deg']:.0f}'):>6} {str(aircraft['on_ground']):>7} {aircraft['age_s']:7.0f}")
        return 0 if success else 2
    finally:
        await recorder.stop()
        await source.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Print live OpenSky aircraft around a configured airport")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--once", action="store_true", help="fetch once and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        return asyncio.run(run(load_config(args.config), args.once))
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
