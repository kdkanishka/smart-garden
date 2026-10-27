from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import OperationalError, TimeoutError as DatabaseTimeout

import main
from database import get_session
from models import Plant


@pytest.fixture
def api():
    app = main.create_app()
    session = object()

    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    app.state.weather_client = AsyncMock(fetch=AsyncMock(return_value=None))
    # No lifespan or actual database; route collaborators are mocked in each test.
    return TestClient(app), session


@pytest.fixture
def plant():
    return Plant(
        id=1, name="Mint", lat=Decimal("57.700000"), lon=Decimal("11.900000"),
        created_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
        updated_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("method,path,operation,status", [
    ("post", "/plants", "create_plant", 201),
    ("get", "/plants/1", "get_plant", 200),
    ("patch", "/plants/1", "update_plant", 200),
])
def test_stored_plant_response(api, plant, monkeypatch, method, path, operation, status):
    client, session = api
    persistence = AsyncMock(return_value=plant)
    monkeypatch.setattr(main.crud, operation, persistence)
    kwargs = {} if method == "get" else {"json": {"name": "Mint", "lat": 57.7, "lon": 11.9}}
    response = client.request(method, path, **kwargs)
    assert response.status_code == status
    expected = {
        "id": 1, "name": "Mint", "lat": 57.7, "lon": 11.9,
        "created_at": "2026-10-27T00:00:00Z", "updated_at": "2026-10-27T00:00:00Z",
    }
    if method == "get":
        expected.update(weather_status="unavailable", weather=None)
    assert response.json() == expected
    assert persistence.await_args.args[0] is session


def test_list_and_delete(api, plant, monkeypatch):
    client, session = api
    monkeypatch.setattr(main.crud, "list_plants", AsyncMock(return_value=[plant]))
    assert client.get("/plants").json()[0]["id"] == 1
    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(main.crud, "delete_plant", delete)
    response = client.delete("/plants/1")
    assert response.status_code == 204
    assert response.content == b""
    delete.assert_awaited_once_with(session, 1)


@pytest.mark.parametrize("method,operation,result", [
    ("get", "get_plant", None), ("patch", "update_plant", None),
    ("delete", "delete_plant", False),
])
def test_unknown_ids_return_404(api, monkeypatch, method, operation, result):
    client, _ = api
    monkeypatch.setattr(main.crud, operation, AsyncMock(return_value=result))
    kwargs = {"json": {"name": "Rose"}} if method == "patch" else {}
    response = client.request(method, "/plants/99", **kwargs)
    assert response.status_code == 404
    assert response.json() == {"detail": "Plant not found"}


@pytest.mark.parametrize("method,data", [
    ("post", {}), ("post", {"name": "Mint", "lat": 0}),
    ("post", {"name": "Mint", "lat": 0, "lon": 0, "extra": 1}),
    ("patch", {}), ("patch", {"name": None}), ("patch", {"lat": None}),
    ("patch", {"lon": None}), ("patch", {"extra": 1}),
    ("patch", {"lat": "90.00000001"}), ("patch", {"name": "  "}),
])
def test_validation_returns_fastapi_422_before_persistence(api, monkeypatch, method, data):
    client, _ = api
    operation = AsyncMock(side_effect=AssertionError("Must not persist invalid requests"))
    monkeypatch.setattr(main.crud, "create_plant" if method == "post" else "update_plant", operation)
    response = client.request(method, "/plants" if method == "post" else "/plants/1", json=data)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert response.json()["detail"][0]["loc"][0] == "body"
    operation.assert_not_awaited()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_numbers_return_serializable_422(api, value):
    client, _ = api
    response = client.patch(
        "/plants/1", content='{"lat": ' + value + '}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "lat"]


@pytest.mark.parametrize("method,path,operation,data", [
    ("post", "/plants", "create_plant", {"name": "Mint", "lat": 0, "lon": 0}),
    ("get", "/plants", "list_plants", None),
    ("get", "/plants/1", "get_plant", None),
    ("patch", "/plants/1", "update_plant", {"name": "Mint"}),
    ("delete", "/plants/1", "delete_plant", None),
])
@pytest.mark.parametrize("failure", [
    OperationalError("query", {}, Exception("private database credentials")),
    DatabaseTimeout("pool exhausted"),
])
def test_database_failures_are_503(api, monkeypatch, method, path, operation, data, failure):
    client, _ = api
    monkeypatch.setattr(main.crud, operation, AsyncMock(side_effect=failure))
    response = client.request(method, path, **({"json": data} if data else {}))
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


def test_health_checks(api, monkeypatch):
    client, session = api
    check = AsyncMock()
    monkeypatch.setattr(main, "check_schema", check)
    assert client.get("/health/live").status_code == 200
    check.assert_not_awaited()
    assert client.get("/health/ready").status_code == 200
    check.assert_awaited_once_with(session)
    check.side_effect = OperationalError("query", {}, Exception())
    assert client.get("/health/ready").status_code == 503
    assert client.get("/health/live").status_code == 200


def test_readiness_without_database_initialization_is_503():
    client = TestClient(main.create_app())
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503
