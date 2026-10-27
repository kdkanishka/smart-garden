import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest
from pydantic import ValidationError

from main import Settings, create_app


START = datetime(2026, 10, 27, 9, tzinfo=timezone.utc)
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "snow1g.json").read_text()
)


class Clock:
    def __init__(self):
        self.seconds = 0

    def utc_now(self):
        return START + timedelta(seconds=self.seconds)

    def monotonic(self):
        return self.seconds


def make_app(handler, clock=None, **config):
    clock = clock or Clock()
    return create_app(
        Settings(**config), transport=httpx.MockTransport(handler),
        utc_now=clock.utc_now, monotonic=clock.monotonic,
    )


def success(request):
    return httpx.Response(200, json=FIXTURE)


def test_weather_contract_and_fixed_smhi_route():
    requests = []

    def upstream(request):
        requests.append(request)
        return success(request)

    with TestClient(make_app(upstream)) as client:
        response = client.get("/api/weather?lat=57.70000049&lon=11.90000049")
    assert response.status_code == 200
    assert response.json() == {
        "rain_expected_mm": 9.6,
        "advice": "Skip watering",
        "window_start": "2026-10-27T09:00:00Z",
        "window_end": "2026-10-29T09:00:00Z",
        "forecast_reference_time": "2026-10-27T06:00:00Z",
        "calculated_at": "2026-10-27T09:00:00Z",
    }
    assert str(requests[0].url) == (
        "https://opendata-download-metfcst.smhi.se/api/category/snow1g/"
        "version/1/geotype/point/lon/11.900000/lat/57.700000/data.json"
    )


@pytest.mark.parametrize("query", [
    "", "lat=57", "lon=12", "lat=word&lon=12", "lat=91&lon=12",
    "lat=-90.0000001&lon=12", "lat=57&lon=180.0000001",
    "lat=57&lon=-181", "lat=nan&lon=12", "lat=inf&lon=12",
    "lat=-inf&lon=12", "lat=57&lon=NaN", "lat=57&lon=1e999",
])
def test_invalid_coordinates_return_fastapi_validation_errors(query):
    def unexpected(request):
        pytest.fail("Invalid coordinates must not call SMHI")

    with TestClient(make_app(unexpected)) as client:
        response = client.get(f"/api/weather?{query}")
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert response.json()["detail"][0]["loc"][0] == "query"


@pytest.mark.parametrize("lat,lon,path", [
    ("90", "180", "/lon/180.000000/lat/90.000000/"),
    ("-90", "-180", "/lon/-180.000000/lat/-90.000000/"),
    ("1e-8", "-0.00000001", "/lon/0.000000/lat/0.000000/"),
    ("57.1234568", "11.9876548", "/lon/11.987655/lat/57.123457/"),
])
def test_coordinate_bounds_rounding_and_decimal_format(lat, lon, path):
    def upstream(request):
        assert path in request.url.path
        return success(request)

    with TestClient(make_app(upstream)) as client:
        assert client.get("/api/weather", params={"lat": lat, "lon": lon}).status_code == 200


@pytest.mark.parametrize("upstream_response", [
    httpx.Response(404, text="No forecast for this location"),
    httpx.Response(500),
    httpx.Response(302, headers={"Location": "https://example.com"}),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={}),
    httpx.Response(200, json={"referenceTime": "2026-10-27T06:00:00Z", "timeSeries": []}),
    httpx.Response(200, json=None),
])
def test_upstream_errors_are_502_and_not_cached(upstream_response):
    calls = 0

    def upstream(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return upstream_response
        return success(request)

    with TestClient(make_app(upstream)) as client:
        response = client.get("/api/weather?lat=57&lon=12")
        assert response.status_code == 502
        assert isinstance(response.json()["detail"], str)
        assert client.get("/api/weather?lat=57&lon=12").status_code == 200
    assert calls == 2


@pytest.mark.parametrize("exception,status", [
    (httpx.ConnectError("connection failed"), 502),
    (httpx.ReadTimeout("read timeout"), 504),
])
def test_transport_errors(exception, status):
    def upstream(request):
        raise exception

    with TestClient(make_app(upstream)) as client:
        response = client.get("/api/weather?lat=57&lon=12")
    assert response.status_code == status
    assert "detail" in response.json()


def test_overall_deadline_includes_slow_response_read_and_does_not_cache_failure():
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            # Each chunk arrives within a per-operation timeout, but the
            # response as a whole exceeds the overall request deadline.
            for _ in range(100):
                await asyncio.sleep(0.005)
                yield b" "

    calls = 0

    def upstream(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, stream=SlowStream())
        return success(request)

    with TestClient(make_app(upstream, smhi_timeout_seconds=0.03)) as client:
        assert client.get("/api/weather?lat=57&lon=12").status_code == 504
        assert client.get("/api/weather?lat=57&lon=12").status_code == 200
    assert calls == 2


def test_cache_normalizes_coordinates_preserves_timestamps_and_expires_at_ttl():
    clock = Clock()
    calls = 0

    def upstream(request):
        nonlocal calls
        calls += 1
        clock.seconds += 1
        return success(request)

    with TestClient(make_app(upstream, clock)) as client:
        first = client.get("/api/weather?lat=57.7000001&lon=11.9000001").json()
        assert first["calculated_at"] == "2026-10-27T09:00:01Z"
        clock.seconds = 300  # TTL starts at completion, not request start.
        assert client.get("/api/weather?lat=57.7&lon=11.9").json() == first
        assert calls == 1
        clock.seconds = 301
        second = client.get("/api/weather?lat=57.7&lon=11.9").json()
        assert second["window_start"] == "2026-10-27T09:05:01Z"
        assert second["calculated_at"] == "2026-10-27T09:05:02Z"
        assert calls == 2


def test_cache_capacity_uses_least_recently_used_eviction():
    calls = []

    def upstream(request):
        calls.append(request.url.path)
        return success(request)

    with TestClient(make_app(upstream, weather_cache_max_entries=2)) as client:
        for lat in [1, 2, 1, 3, 1, 2]:
            assert client.get(f"/api/weather?lat={lat}&lon=12").status_code == 200
    assert len(calls) == 4
    assert [path.split("/lat/")[1].split("/")[0] for path in calls] == [
        "1.000000", "2.000000", "3.000000", "2.000000",
    ]


def test_health_checks_do_not_call_smhi():
    def unexpected(request):
        pytest.fail("Health probes must not call SMHI")

    app = make_app(unexpected)
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ok"}
    assert app.state.analyzer is None
    with TestClient(app, raise_server_exceptions=False) as client:
        client.app.state.analyzer = None
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/live").status_code == 200


def test_configured_threshold_is_used():
    with TestClient(make_app(success, watering_threshold_mm=10)) as client:
        response = client.get("/api/weather?lat=57&lon=12")
    assert response.json()["advice"] == "Consider watering"


@pytest.mark.parametrize("name,value", [
    ("WATERING_THRESHOLD_MM", "-1"), ("WATERING_THRESHOLD_MM", "nan"),
    ("WATERING_THRESHOLD_MM", "inf"), ("SMHI_TIMEOUT_SECONDS", "0"),
    ("SMHI_TIMEOUT_SECONDS", "nan"), ("WEATHER_CACHE_TTL_SECONDS", "-1"),
    ("WEATHER_CACHE_TTL_SECONDS", "inf"), ("WEATHER_CACHE_MAX_ENTRIES", "0"),
    ("WEATHER_CACHE_MAX_ENTRIES", "1.5"), ("SMHI_TIMEOUT_SECONDS", "bad"),
])
def test_invalid_environment_configuration_fails_at_startup(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError), TestClient(create_app()):
        pass


def test_environment_configuration(monkeypatch):
    for name, value in {
        "WATERING_THRESHOLD_MM": "0", "SMHI_TIMEOUT_SECONDS": "2.5",
        "WEATHER_CACHE_TTL_SECONDS": "30", "WEATHER_CACHE_MAX_ENTRIES": "2",
    }.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    assert settings == Settings(
        watering_threshold_mm=0, smhi_timeout_seconds=2.5,
        weather_cache_ttl_seconds=30, weather_cache_max_entries=2,
    )


def test_concurrent_misses_coalesce_and_distinct_locations_run_concurrently():
    async def scenario():
        calls = 0
        both_started = asyncio.Event()
        release = asyncio.Event()

        async def upstream(request):
            nonlocal calls
            calls += 1
            if calls == 2:
                both_started.set()
            await release.wait()
            return success(request)

        app = make_app(upstream)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
            ) as client:
                tasks = [asyncio.create_task(client.get(
                    "/api/weather", params={"lat": lat, "lon": 12},
                )) for lat in [57.7] * 10 + [58.7] * 10]
                await asyncio.wait_for(both_started.wait(), timeout=1)
                release.set()
                responses = await asyncio.gather(*tasks)
                assert calls == 2
                assert all(response.status_code == 200 for response in responses)
                assert all(response.json() == responses[0].json() for response in responses)
        assert app.state.analyzer is None

    asyncio.run(scenario())


def test_failed_concurrent_miss_is_shared_then_retried():
    async def scenario():
        calls = 0
        started = asyncio.Event()
        release = asyncio.Event()

        async def upstream(request):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return httpx.Response(503) if calls == 1 else success(request)

        app = make_app(upstream)
        async with app.router.lifespan_context(app):
            analyzer = app.state.analyzer
            tasks = [asyncio.create_task(analyzer.get(57, 12)) for _ in range(10)]
            await started.wait()
            release.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert calls == 1
            assert all(isinstance(result, Exception) for result in results)
            assert not analyzer.cache
            assert (await analyzer.get(57, 12))["rain_expected_mm"] == 9.6
            assert calls == 2

    asyncio.run(scenario())


def test_cancelled_waiter_does_not_cancel_shared_calculation_and_shutdown_cleans_up():
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def upstream(request):
            started.set()
            await release.wait()
            return success(request)

        app = make_app(upstream)
        async with app.router.lifespan_context(app):
            analyzer = app.state.analyzer
            cancelled = asyncio.create_task(analyzer.get(57, 12))
            await started.wait()
            survivor = asyncio.create_task(analyzer.get(57, 12))
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            release.set()
            assert (await survivor)["rain_expected_mm"] == 9.6

            release.clear()
            started.clear()
            unfinished = asyncio.create_task(analyzer.get(58, 12))
            await started.wait()
        with pytest.raises(asyncio.CancelledError):
            await unfinished
        assert not analyzer.inflight
        assert not analyzer.cache

    asyncio.run(scenario())
