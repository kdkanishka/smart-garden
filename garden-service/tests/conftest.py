import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def public_key(signing_key):
    return signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


@pytest.fixture(autouse=True)
def jwt_environment(monkeypatch, public_key):
    monkeypatch.setenv("JWT_PUBLIC_KEY", public_key)
