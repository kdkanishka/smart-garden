"""Real PostgreSQL ownership checks; use only a disposable *_test database."""

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from unittest.mock import AsyncMock

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from database import Settings, create_database
from main import create_app


@pytest.fixture
def database_url(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL isolation checks")
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a disposable database ending in _test")
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")

    async def clear():
        engine = create_database(Settings(database_url=url))
        try:
            async with engine.begin() as connection:
                await connection.execute(text("TRUNCATE plants RESTART IDENTITY"))
        finally:
            await engine.dispose()

    asyncio.run(clear())
    yield url
    asyncio.run(clear())


def test_private_gardens_with_real_database(database_url, public_key, signing_key):
    app = create_app(Settings(database_url=database_url, jwt_public_key=public_key))
    now = datetime.now(timezone.utc)

    def headers(subject):
        token = jwt.encode({"sub": subject, "iss": "smart-garden-auth", "aud": "smart-garden",
                            "iat": now, "exp": now + timedelta(minutes=30)}, signing_key, algorithm="RS256")
        return {"Authorization": f"Bearer {token}"}

    first, second = headers("demo-user-1"), headers("demo-user-2")
    with TestClient(app) as client:
        app.state.weather_client = AsyncMock(fetch=AsyncMock(return_value=None))
        assert client.get("/health/ready").status_code == 200
        assert client.get("/plants").status_code == 401
        response = client.post("/plants", headers=first, json={"name": "Mint", "lat": 57.7, "lon": 11.9})
        assert response.status_code == 201
        plant_id = response.json()["id"]
        assert client.get("/plants", headers=second).json() == []
        assert client.get(f"/plants/{plant_id}", headers=second).status_code == 404
        assert client.patch(f"/plants/{plant_id}", headers=second, json={"name": "Stolen"}).status_code == 404
        assert client.delete(f"/plants/{plant_id}", headers=second).status_code == 404
        assert client.patch(f"/plants/{plant_id}", headers=first, json={"owner_id": "demo-user-2"}).status_code == 422
        assert client.get(f"/plants/{plant_id}", headers=first).json()["name"] == "Mint"
        assert client.patch(f"/plants/{plant_id}", headers=first, json={"name": "Basil"}).status_code == 200
        response = client.post("/plants", headers=second, json={"name": "Rose", "lat": 0, "lon": 0})
        assert response.status_code == 201
        second_id = response.json()["id"]
        assert [p["name"] for p in client.get("/plants", headers=first).json()] == ["Basil"]
        assert [p["name"] for p in client.get("/plants", headers=second).json()] == ["Rose"]

    # A new application pool sees the same records and ownership.
    with TestClient(create_app(Settings(database_url=database_url, jwt_public_key=public_key))) as client:
        assert client.delete(f"/plants/{second_id}", headers=first).status_code == 404
        assert client.delete(f"/plants/{plant_id}", headers=first).status_code == 204
        assert client.delete(f"/plants/{second_id}", headers=second).status_code == 204
