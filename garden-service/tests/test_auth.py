from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import jwt
import pytest

import main
from database import Settings, get_session


def access_token(key, **overrides):
    now = datetime.now(timezone.utc)
    claims = {"sub": "demo-user-1", "iss": "smart-garden-auth", "aud": "smart-garden",
              "iat": now, "exp": now + timedelta(minutes=30)}
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.fixture
def client(public_key):
    app = main.create_app()
    app.state.auth_settings = Settings(database_url="postgresql+psycopg://localhost/test", jwt_public_key=public_key)

    async def session():
        yield object()

    app.dependency_overrides[get_session] = session
    return TestClient(app)


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/plants", None), ("GET", "/plants/1", None),
    ("POST", "/plants", {"name": "Mint", "lat": 0, "lon": 0}),
    ("PATCH", "/plants/1", {"name": "Rose"}), ("DELETE", "/plants/1", None),
])
def test_all_plant_endpoints_require_login(client, monkeypatch, method, path, body):
    operations = [AsyncMock() for _ in range(5)]
    for name, operation in zip(("list_plants", "get_plant", "create_plant", "update_plant", "delete_plant"), operations):
        monkeypatch.setattr(main.crud, name, operation)
    result = client.request(method, path, **({"json": body} if body else {}))
    assert result.status_code == 401
    assert result.headers["www-authenticate"] == "Bearer"
    for operation in operations:
        operation.assert_not_awaited()


@pytest.mark.parametrize("claims", [
    {"exp": 1}, {"iss": "other"}, {"aud": "other"}, {"sub": ""},
    {"sub": 123}, {"iat": datetime.now(timezone.utc) + timedelta(hours=1)},
])
def test_invalid_claims_rejected(client, signing_key, claims):
    token = access_token(signing_key, **claims)
    assert client.get("/plants", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_missing_claim_wrong_key_and_algorithm_rejected(client, signing_key):
    from cryptography.hazmat.primitives.asymmetric import rsa

    tokens = ["not-a-token", access_token(rsa.generate_private_key(public_exponent=65537, key_size=2048)),
              jwt.encode({"sub": "demo-user-1"}, "some-secret", algorithm="HS256")]
    for missing in ("sub", "iss", "aud", "exp", "iat"):
        claims = jwt.decode(access_token(signing_key), options={"verify_signature": False})
        del claims[missing]
        tokens.append(jwt.encode(claims, signing_key, algorithm="RS256"))
    for token in tokens:
        assert client.get("/plants", headers={"Authorization": f"Bearer {token}"}).status_code == 401


@pytest.mark.parametrize("subject", ["demo-user-1", "demo-user-2"])
def test_verified_subject_passed_to_persistence(client, signing_key, monkeypatch, subject):
    listing = AsyncMock(return_value=[])
    monkeypatch.setattr(main.crud, "list_plants", listing)
    result = client.get("/plants", headers={"Authorization": f"Bearer {access_token(signing_key, sub=subject)}"})
    assert result.status_code == 200 and result.json() == []
    assert listing.await_args.args[-1] == subject


def test_health_does_not_require_token(client):
    assert client.get("/health/live").status_code == 200
