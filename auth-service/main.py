"""Fixed demo accounts and short-lived RS256 access tokens."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Response
import jwt
from pydantic import BaseModel, ConfigDict, Field


# Stable IDs must not change: plants refer to these JWT subjects.
ACCOUNTS = {
    "demo1": ("demo-user-1", "1ceaabb23fb5cfb5b181d5c607d8d7ee2f0ced4f6724ab570d9221aa72abb3bf"),
    "demo2": ("demo-user-2", "586f75a2fb04052000e8773d6afcbbc7875dff643b54a8a4e50750e8560e7ca8"),
}


class Settings(BaseModel):
    jwt_private_key: str
    jwt_issuer: str = "smart-garden-auth"
    jwt_audience: str = "smart-garden"

    @classmethod
    def from_env(cls):
        return cls(**{name: os.environ[name.upper()] for name in cls.model_fields if name.upper() in os.environ})


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 1800
    username: str


def create_app(settings: Settings | None = None):
    @asynccontextmanager
    async def lifespan(app):
        config = settings or Settings.from_env()
        key = serialization.load_pem_private_key(config.jwt_private_key.encode(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ValueError("JWT_PRIVATE_KEY must be an RSA private key of at least 2048 bits")
        app.state.config = config
        app.state.key = key
        yield
        app.state.key = None

    app = FastAPI(title="Auth Service", lifespan=lifespan)

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        if getattr(app.state, "key", None) is None:
            raise HTTPException(503, "Signing key is not initialized")
        return {"status": "ok"}

    @app.post("/login", response_model=TokenResponse)
    def login(data: Login, response: Response):
        # Do the same expensive hash for unknown usernames as for known ones.
        known = data.username in ACCOUNTS
        account_name = data.username if known else "demo1"
        subject, expected = ACCOUNTS[account_name]
        actual = hashlib.pbkdf2_hmac(
            "sha256", data.password.encode(), f"smart-garden-{account_name}-v1".encode(), 600000,
        ).hex()
        if not hmac.compare_digest(actual, expected) or not known:
            raise HTTPException(401, "Invalid username or password", headers={"WWW-Authenticate": "Bearer"})
        now = datetime.now(timezone.utc)
        config = app.state.config
        token = jwt.encode({
            "sub": subject, "iss": config.jwt_issuer, "aud": config.jwt_audience,
            "iat": now, "exp": now + timedelta(minutes=30),
        }, app.state.key, algorithm="RS256")
        response.headers["Cache-Control"] = "no-store"
        return TokenResponse(access_token=token, username=data.username)

    return app


app = create_app()
