#!/usr/bin/env python3
"""Print the real NOAA response and local decoded weather for the configured airport."""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml

from backend.airwatch.weather import LiveWeatherService


async def main():
    config = yaml.safe_load((ROOT / "config.yaml").read_text())
    settings = config["weather"]
    airport = config["airport"]["icao"]
    url = f"{settings['metar_url']}?ids={airport}&format=json"
    print(f"Request URL: {url}")
    service = LiveWeatherService(airport=airport, settings=settings)
    try:
        await service.refresh()
        data = service.snapshot()
        diagnostic = data["provider_diagnostics"].get("metar") or {}
        print(f"HTTP status: {diagnostic.get('status_code', 'unavailable')}")
        print("Raw provider JSON:")
        print(json.dumps(diagnostic.get("json", data["metar"].get("report")), indent=2, ensure_ascii=False))
        print("Decoded values:")
        print(json.dumps(data["metar"].get("decoded"), indent=2, ensure_ascii=False))
        if not data["metar"].get("report"):
            print("Reason: " + str(data["metar"].get("error") or "NOAA returned no report for the requested station."))
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
