import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError
import pytest

import main
from auth import current_user
from database import Settings, get_session
from models import Plant
from schemas import WeatherResponse
from weather_client import WeatherClient, enrich_plants


WEATHER = {
    "rain_expected_mm": 15.2,
    "advice": "Skip watering",
    "window_start": "2026-10-27T12:00:00Z",
    "window_end": "2026-10-29T12:00:00Z",
    "forecast_reference_time": "2026-10-27T11:00:00Z",
    "calculated_at": "2026-10-27T12:00:01Z",
}
DATABASE_URL = "postgresql+psycopg://user:password@localhost/garden_test"


def plant(plant_id=1, lat="57.7", lon="11.9"):
    return Plant(
        id=plant_id, name="Mint", lat=Decimal(lat), lon=Decimal(lon),
        created_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
        updated_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
    )


@pytest.fixture
def api_factory(monkeypatch, public_key):
    engine = Mock(dispose=AsyncMock())
    monkeypatch.setattr(main, "create_database", Mock(return_value=engine))
    monkeypatch.setattr(main, "create_session_factory", Mock(return_value=Mock()))

    @contextmanager
    def factory(handler, **settings):
        app = main.create_app(
            Settings(database_url=DATABASE_URL, jwt_public_key=public_key, **settings),
            transport=httpx.MockTransport(handler),
        )

        async def session():
            yield object()

        app.dependency_overrides[get_session] = session
        app.dependency_overrides[current_user] = lambda: "demo-user-1"
        with TestClient(app) as client:
            yield client
        assert app.state.weather_client is None
        assert app.state.session_factory is None
        engine.dispose.assert_awaited()

    return factory


@pytest.mark.parametrize("path", ["/plants", "/plants/1"])
def test_enriched_reads_use_analyzer_contract(api_factory, monkeypatch, path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=WEATHER)

    monkeypatch.setattr(main.crud, "list_plants", AsyncMock(return_value=[plant()]))
    monkeypatch.setattr(main.crud, "get_plant", AsyncMock(return_value=plant()))
    with api_factory(handler) as client:
        response = client.get(path)
    assert response.status_code == 200
    result = response.json()[0] if path == "/plants" else response.json()
    assert result["weather_status"] == "available"
    assert result["weather"] == WEATHER
    assert result["lat"] == 57.7
    assert result["created_at"].endswith("Z")
    assert len(requests) == 1
    assert requests[0].url.host == "weather-analyzer-svc"
    assert requests[0].url.path == "/api/weather"
    assert dict(requests[0].url.params) == {"lat": "57.700000", "lon": "11.900000"}


@pytest.mark.parametrize("path", ["/plants", "/plants/1"])
@pytest.mark.parametrize("status", [302, 422, 500, 502, 503, 504])
def test_non_success_weather_keeps_reads_available(api_factory, monkeypatch, path, status):
    monkeypatch.setattr(main.crud, "list_plants", AsyncMock(return_value=[plant()]))
    monkeypatch.setattr(main.crud, "get_plant", AsyncMock(return_value=plant()))
    with api_factory(lambda request: httpx.Response(status)) as client:
        response = client.get(path)
    assert response.status_code == 200
    result = response.json()[0] if path == "/plants" else response.json()
    assert result["weather_status"] == "unavailable"
    assert result["weather"] is None


@pytest.mark.parametrize("method,path,operation,result,body,status", [
    ("post", "/plants", "create_plant", plant(), {"name": "Mint", "lat": 0, "lon": 0}, 201),
    ("patch", "/plants/1", "update_plant", plant(), {"name": "Mint"}, 200),
    ("delete", "/plants/1", "delete_plant", True, None, 204),
])
def test_mutations_never_call_weather(api_factory, monkeypatch, method, path, operation, result, body, status):
    handler = Mock(side_effect=AssertionError("Mutation must not call Weather Analyzer"))
    monkeypatch.setattr(main.crud, operation, AsyncMock(return_value=result))
    with api_factory(handler) as client:
        response = client.request(method, path, **({"json": body} if body else {}))
    assert response.status_code == status
    if status != 204:
        assert "weather" not in response.json()
        assert "weather_status" not in response.json()
    handler.assert_not_called()


def test_empty_list_does_not_call_weather(api_factory, monkeypatch):
    handler = Mock(side_effect=AssertionError("No coordinates to look up"))
    monkeypatch.setattr(main.crud, "list_plants", AsyncMock(return_value=[]))
    with api_factory(handler) as client:
        response = client.get("/plants")
    assert response.status_code == 200
    assert response.json() == []
    handler.assert_not_called()


@pytest.mark.parametrize("path", ["/plants", "/plants/1"])
def test_weather_request_deadline_on_reads(api_factory, monkeypatch, path):
    cancelled = []

    async def handler(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(main.crud, "list_plants", AsyncMock(return_value=[plant()]))
    monkeypatch.setattr(main.crud, "get_plant", AsyncMock(return_value=plant()))
    with api_factory(handler, weather_timeout_seconds=0.01) as client:
        response = client.get(path)
    assert response.status_code == 200
    result = response.json()[0] if path == "/plants" else response.json()
    assert result["weather_status"] == "unavailable"
    assert result["weather"] is None
    assert cancelled == [True]


def test_total_deadline_on_list_keeps_partial_weather(api_factory, monkeypatch):
    cancelled = []

    async def handler(request):
        if request.url.params["lon"] == "0.000000":
            return httpx.Response(200, json=WEATHER)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(main.crud, "list_plants", AsyncMock(return_value=[
        plant(i, lon=str(i)) for i in range(10)
    ]))
    with api_factory(handler, weather_enrichment_timeout_seconds=0.02) as client:
        response = client.get("/plants")
    assert response.status_code == 200
    results = response.json()
    assert results[0]["weather"] == WEATHER
    assert results[0]["weather_status"] == "available"
    assert all(item["weather"] is None and item["weather_status"] == "unavailable" for item in results[1:])
    assert len(cancelled) == 5


@pytest.mark.parametrize("payload", [
    None, [], {}, "weather", {key: value for key, value in WEATHER.items() if key != "advice"},
    {**WEATHER, "rain_expected_mm": -1}, {**WEATHER, "rain_expected_mm": float("nan")},
    {**WEATHER, "rain_expected_mm": float("inf")}, {**WEATHER, "rain_expected_mm": True},
    {**WEATHER, "rain_expected_mm": "15.2"}, {**WEATHER, "advice": "Water now"},
    {**WEATHER, "window_start": "not a date"},
    {**WEATHER, "window_start": "2026-10-27T12:00:00"},
    {**WEATHER, "window_start": 1793102400},
    {**WEATHER, "window_end": "2026-10-27T12:00:00Z"},
    {**WEATHER, "window_end": "2026-10-28T12:00:00Z"},
    {**WEATHER, "forecast_reference_time": None},
    {**WEATHER, "calculated_at": "yesterday"},
])
def test_malformed_weather_becomes_unavailable(payload):
    # json.dumps permits NaN/Infinity, allowing rejection of nonstandard upstream JSON.
    import json

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=json.dumps(payload)),
        )) as client:
            return await WeatherClient(client, "http://weather").fetch(Decimal(0), Decimal(0))

    assert asyncio.run(run()) is None


@pytest.mark.parametrize("content", [b"not json", b'{"rain_expected_mm":', b"\xff"])
def test_invalid_json_becomes_unavailable(content):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=content),
        )) as client:
            return await WeatherClient(client, "http://weather").fetch(Decimal(0), Decimal(0))

    assert asyncio.run(run()) is None


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadError, httpx.ReadTimeout])
def test_weather_network_errors_become_unavailable(failure):
    def handler(request):
        raise failure("unavailable", request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await WeatherClient(client, "http://weather").fetch(Decimal(0), Decimal(0))

    assert asyncio.run(run()) is None


@pytest.mark.parametrize("slow_body", [False, True])
def test_weather_deadline_includes_body_reading(slow_body):
    cancelled = []

    async def block():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"{"
            await block()

    async def handler(request):
        if not slow_body:
            await block()
        return httpx.Response(200, stream=SlowBody())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = WeatherClient(client, "http://weather", timeout_seconds=0.01)
            return await asyncio.wait_for(adapter.fetch(Decimal(0), Decimal(0)), timeout=1)

    assert asyncio.run(run()) is None
    assert cancelled == [True]


def test_deduplication_by_rounded_pairs_is_request_local_and_preserves_order():
    async def run():
        adapter = Mock(fetch=AsyncMock(return_value=WeatherResponse.model_validate(WEATHER)))
        plants = [plant(3, "57.7000001"), plant(1, "57.7000002"), plant(2, "57.7", "12")]
        result = await enrich_plants(plants, adapter)
        assert [item.id for item in result] == [3, 1, 2]
        assert all(item.weather_status == "available" for item in result)
        assert result[0].weather == result[1].weather
        assert adapter.fetch.await_count == 2
        await enrich_plants(plants, adapter)
        assert adapter.fetch.await_count == 4

    asyncio.run(run())


def test_failed_coordinate_result_is_shared_only_with_matching_plants():
    async def run():
        async def fetch(lat, lon):
            return WeatherResponse.model_validate(WEATHER) if lon == 12 else None

        adapter = Mock(fetch=AsyncMock(side_effect=fetch))
        result = await enrich_plants([plant(1), plant(2), plant(3, lon="12")], adapter)
        assert [item.weather_status for item in result] == ["unavailable", "unavailable", "available"]
        assert adapter.fetch.await_count == 2

    asyncio.run(run())


def test_maximum_five_concurrent_lookups():
    async def run():
        active = 0
        peak = 0
        first_batch = asyncio.Event()
        release = asyncio.Event()

        async def fetch(lat, lon):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 5:
                first_batch.set()
            try:
                await release.wait()
                await asyncio.sleep(0)
                return WeatherResponse.model_validate(WEATHER)
            finally:
                active -= 1

        adapter = Mock(fetch=AsyncMock(side_effect=fetch))
        task = asyncio.create_task(enrich_plants([plant(i, lon=str(i)) for i in range(13)], adapter))
        await asyncio.wait_for(first_batch.wait(), 1)
        assert adapter.fetch.await_count == 5
        release.set()
        result = await task
        assert peak == 5
        assert active == 0
        assert adapter.fetch.await_count == 13
        assert all(item.weather_status == "available" for item in result)

    asyncio.run(run())


def test_total_deadline_keeps_completed_results_and_cancels_pending_workers():
    async def run():
        cancelled = []

        async def fetch(lat, lon):
            if lon == 0:
                return WeatherResponse.model_validate(WEATHER)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(lon)

        adapter = Mock(fetch=AsyncMock(side_effect=fetch))
        result = await asyncio.wait_for(enrich_plants(
            [plant(i, lon=str(i)) for i in range(12)], adapter,
            timeout_seconds=0.02,
        ), timeout=1)
        assert result[0].weather_status == "available"
        assert all(item.weather_status == "unavailable" and item.weather is None for item in result[1:])
        assert len(cancelled) == 5
        assert adapter.fetch.await_count == 6
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    asyncio.run(run())


def test_request_cancellation_cleans_up_weather_workers():
    async def run():
        started = asyncio.Event()
        cancelled = []

        async def fetch(lat, lon):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

        task = asyncio.create_task(enrich_plants([plant()], Mock(fetch=fetch)))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled == [True]

    asyncio.run(run())


@pytest.mark.parametrize("field,value", [
    ("weather_timeout_seconds", 0), ("weather_timeout_seconds", float("inf")),
    ("weather_timeout_seconds", float("nan")),
    ("weather_enrichment_timeout_seconds", -1),
    ("weather_enrichment_timeout_seconds", float("inf")),
    ("weather_max_concurrency", 0), ("weather_max_concurrency", 6),
    ("weather_max_concurrency", 1.5),
    ("weather_service_url", "not a URL"), ("weather_service_url", "ftp://weather"),
])
def test_invalid_weather_configuration(field, value):
    with pytest.raises(ValidationError):
        Settings(database_url=DATABASE_URL, **{field: value})


def test_weather_defaults_and_environment(monkeypatch):
    defaults = Settings(database_url=DATABASE_URL)
    assert defaults.weather_timeout_seconds == 12
    assert defaults.weather_max_concurrency == 5
    assert defaults.weather_enrichment_timeout_seconds == 45
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("WEATHER_SERVICE_URL", "http://custom-weather:8000")
    monkeypatch.setenv("WEATHER_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("WEATHER_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("WEATHER_ENRICHMENT_TIMEOUT_SECONDS", "7")
    settings = Settings.from_env()
    assert str(settings.weather_service_url) == "http://custom-weather:8000/"
    assert settings.weather_timeout_seconds == 2
    assert settings.weather_max_concurrency == 3
    assert settings.weather_enrichment_timeout_seconds == 7
