from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
import pytest

from main import Settings, create_app


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def client(key):
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    with TestClient(create_app(Settings(jwt_private_key=pem))) as client:
        yield client


@pytest.mark.parametrize("username,subject", [("demo1", "demo-user-1"), ("demo2", "demo-user-2")])
def test_login_issues_verifiable_token(client, key, username, subject):
    response = client.post("/login", json={"username": username, "password": username})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["token_type"] == "bearer" and result["expires_in"] == 1800
    assert result["username"] == username
    claims = jwt.decode(result["access_token"], key.public_key(), algorithms=["RS256"],
                        issuer="smart-garden-auth", audience="smart-garden")
    assert claims["sub"] == subject
    assert claims["exp"] - claims["iat"] == 1800
    assert abs(claims["iat"] - datetime.now(timezone.utc).timestamp()) < 5
    assert "password" not in claims


@pytest.mark.parametrize("username,password", [("demo1", "wrong"), ("unknown", "demo1"), ("demo2", "demo1")])
def test_bad_credentials_are_generic(client, username, password):
    result = client.post("/login", json={"username": username, "password": password})
    assert result.status_code == 401
    assert result.json() == {"detail": "Invalid username or password"}


def test_health_and_input_validation(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.post("/login", json={"username": "demo1", "password": "demo1", "sub": "other"}).status_code == 422


def test_invalid_signing_key_blocks_startup():
    with pytest.raises(ValueError):
        with TestClient(create_app(Settings(jwt_private_key="invalid"))):
            pass
