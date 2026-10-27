"""Weather Analyzer HTTP API. Run with uvicorn main:app."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import time
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
import httpx
from pydantic import BaseModel, ConfigDict, Field

from smhi_client import SMHIClient, UpstreamError, UpstreamTimeout
from weather import WeatherAnalyzer


class Settings(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, frozen=True)

    watering_threshold_mm: float = Field(default=5.0, ge=0)
    weather_cache_ttl_seconds: float = Field(default=300, gt=0)
    weather_cache_max_entries: int = Field(default=1000, gt=0)
    smhi_timeout_seconds: float = Field(default=10, gt=0)

    @classmethod
    def from_env(cls):
        return cls(**{
            field: os.environ[field.upper()]
            for field in cls.model_fields
            if field.upper() in os.environ
        })


class WeatherResponse(BaseModel):
    rain_expected_mm: float
    advice: Literal["Skip watering", "Consider watering"]
    window_start: str
    window_end: str
    forecast_reference_time: str
    calculated_at: str


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    utc_now=lambda: datetime.now(timezone.utc),
    monotonic=time.monotonic,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = settings if settings is not None else Settings.from_env()
        async with httpx.AsyncClient(
            transport=transport, timeout=config.smhi_timeout_seconds,
            follow_redirects=False,
        ) as client:
            analyzer = WeatherAnalyzer(
                SMHIClient(client, config.smhi_timeout_seconds),
                threshold_mm=config.watering_threshold_mm,
                ttl_seconds=config.weather_cache_ttl_seconds,
                max_entries=config.weather_cache_max_entries,
                utc_now=utc_now,
                monotonic=monotonic,
            )
            app.state.analyzer = analyzer
            try:
                yield
            finally:
                app.state.analyzer = None
                await analyzer.close()

    app = FastAPI(title="Weather Analyzer", lifespan=lifespan)

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready(request: Request):
        if getattr(request.app.state, "analyzer", None) is None:
            raise HTTPException(503, "Weather Analyzer is not initialized")
        return {"status": "ok"}

    @app.get(
        "/api/weather", response_model=WeatherResponse,
        responses={422: {"description": "Invalid coordinates"},
                   502: {"description": "Unusable upstream forecast"},
                   504: {"description": "SMHI deadline exceeded"}},
    )
    async def weather(
        request: Request,
        lat: Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)],
        lon: Annotated[float, Query(ge=-180, le=180, allow_inf_nan=False)],
    ):
        # Validate bounds before rounding; normalize signed zero as well.
        lat, lon = round(lat, 6) or 0.0, round(lon, 6) or 0.0
        try:
            return await request.app.state.analyzer.get(lat, lon)
        except UpstreamTimeout as exc:
            raise HTTPException(504, str(exc)) from exc
        except UpstreamError as exc:
            raise HTTPException(502, str(exc)) from exc

    return app


app = create_app()
