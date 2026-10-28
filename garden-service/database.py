"""Asynchronous PostgreSQL connections; schema changes belong to Alembic."""

import os

from fastapi import HTTPException, Request
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


SCHEMA_REVISION = "0001_create_plants"


class Base(DeclarativeBase):
    pass


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, validate_default=True)

    database_url: str
    jwt_public_key: str = ""
    jwt_issuer: str = "smart-garden-auth"
    jwt_audience: str = "smart-garden"
    weather_service_url: AnyHttpUrl = "http://weather-analyzer-svc:80"
    weather_timeout_seconds: float = Field(default=12, gt=0)
    weather_max_concurrency: int = Field(default=5, gt=0, le=5)
    weather_enrichment_timeout_seconds: float = Field(default=45, gt=0)

    @field_validator("database_url")
    @classmethod
    def require_psycopg(cls, value):
        try:
            url = make_url(value)
        except ArgumentError as exc:
            raise ValueError("DATABASE_URL must be a valid SQLAlchemy URL") from exc
        if url.drivername != "postgresql+psycopg" or not url.database:
            raise ValueError("DATABASE_URL must use postgresql+psycopg and name a database")
        return value

    @classmethod
    def from_env(cls):
        return cls(**{
            field: os.environ[field.upper()]
            for field in cls.model_fields
            if field.upper() in os.environ
        })


def create_database(settings: Settings):
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_timeout=5,
        connect_args={
            "connect_timeout": 5,
            "options": "-c timezone=UTC -c statement_timeout=5000 -c lock_timeout=5000",
        },
    )


def create_session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request):
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(503, "Database is not initialized")
    # Closing the session rolls back any unsuccessful transaction.
    async with factory() as session:
        yield session


async def check_schema(session):
    from models import Plant

    revisions = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    if revisions != [SCHEMA_REVISION]:
        raise HTTPException(503, "Database schema is not up to date")
    # Verify the table and every expected column, including on an empty database.
    await session.execute(select(Plant).limit(0))
