import asyncio
import sqlite3

import httpx

from backend.airwatch.airport_reference import AirportReferenceIndex
from backend.airwatch.enrichment import EnrichmentService


def test_adsbdb_info_cache_ttl_airport_resolution_and_metadata_only(tmp_path):
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        if "/callsign/" in request.url.path:
            return httpx.Response(200,json={"response":{"flightroute":{"airline":{"name":"Example Airline"},
                "origin":{"icao_code":"VOMM"},"destination":{"icao_code":"VOCI"}}}})
        return httpx.Response(200,json={"response":{"aircraft":{"registration":"VT-ABC","type":"A320","registered_owner":"Example Operator"}}})
    async def run():
        clock=[0.0]
        csv=tmp_path/"airports.csv"
        csv.write_text("ident,icao_code,iata_code,name,municipality,latitude_deg,longitude_deg,elevation_ft\n"
                       "VOMM,VOMM,MAA,Chennai International Airport,Chennai,12.99,80.17,52\n"
                       "VOCI,VOCI,COK,Cochin International Airport,Kochi,10.15,76.40,9\n",encoding="utf-8")
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        settings={"base_url":"https://unit.test/v0","timeout_s":1,"max_requests_per_window":10,
            "rate_window_s":60,"callsign_ttl_s":10,"aircraft_ttl_s":20,"failure_backoff_s":5}
        service=EnrichmentService(settings=settings,db_path=tmp_path/"db.sqlite",airport_index=AirportReferenceIndex(csv,tmp_path/"ref.sqlite"),client=client,monotonic=lambda:clock[0],wall_time=lambda:100+clock[0])
        one=await service.info(callsign="  ABC123  ",icao24="abc123")
        assert one["airline"]=="Example Airline"
        assert one["route"]["destination"]["municipality"]=="Kochi"
        assert one["aircraft"]["registration"]=="VT-ABC"
        assert len(calls)==2
        await service.info(callsign="ABC123",icao24="abc123")
        assert len(calls)==2
        clock[0]=11
        await service.info(callsign="ABC123",icao24="abc123")
        assert len(calls)==3  # callsign TTL expired; aircraft cache remains fresh
        await client.aclose()
        with sqlite3.connect(tmp_path/"db.sqlite") as db:
            rows=db.execute("SELECT entity_type,lookup_key,source,negative_cache FROM enrichment_cache ORDER BY entity_type").fetchall()
            db_text=" ".join(map(str,rows))
            assert "Example Airline" not in db_text and "VT-ABC" not in db_text
            assert len(rows)==2
    asyncio.run(run())


def test_failed_lookup_is_negative_cached_in_memory_backoff(tmp_path):
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(503)
    async def run():
        clock=[0.0]
        csv=tmp_path/"airports.csv"
        csv.write_text("ident,icao_code,iata_code,name,municipality,latitude_deg,longitude_deg,elevation_ft\n",encoding="utf-8")
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        settings={"base_url":"https://unit.test/v0","timeout_s":1,"max_requests_per_window":10,
            "rate_window_s":60,"callsign_ttl_s":10,"aircraft_ttl_s":20,"failure_backoff_s":5}
        service=EnrichmentService(settings=settings,db_path=tmp_path/"db.sqlite",airport_index=AirportReferenceIndex(csv,tmp_path/"ref.sqlite"),client=client,monotonic=lambda:clock[0],wall_time=lambda:100)
        result=await service.info(callsign="ABC123",icao24="abc123")
        assert result["airline"]=="Unknown" and result["route"]["available"] is False
        before=len(calls)
        await service.info(callsign="ABC123",icao24="abc123")
        assert len(calls)==before
        with sqlite3.connect(tmp_path/"db.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM enrichment_cache WHERE negative_cache=1").fetchone()[0]==2
        await client.aclose()
    asyncio.run(run())
