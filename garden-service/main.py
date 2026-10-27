"""Garden Service plant API with weather enrichment. Run with uvicorn main:app."""

from contextlib import asynccontextmanager
import math
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import httpx
from sqlalchemy.exc import DBAPIError, TimeoutError as DatabaseTimeout
from sqlalchemy.ext.asyncio import AsyncSession

import crud
from database import Settings, check_schema, create_database, create_session_factory, get_session
from schemas import EnrichedPlantResponse, PlantCreate, PlantPatch, PlantResponse
from weather_client import WeatherClient, enrich_plants


Session = Annotated[AsyncSession, Depends(get_session)]


def create_app(
    settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = settings if settings is not None else Settings.from_env()
        engine = create_database(config)
        app.state.session_factory = create_session_factory(engine)
        try:
            async with httpx.AsyncClient(
                transport=transport, timeout=config.weather_timeout_seconds,
                follow_redirects=False,
            ) as client:
                app.state.weather_client = WeatherClient(
                    client, str(config.weather_service_url), config.weather_timeout_seconds,
                )
                app.state.weather_max_concurrency = config.weather_max_concurrency
                app.state.weather_enrichment_timeout_seconds = config.weather_enrichment_timeout_seconds
                try:
                    yield
                finally:
                    app.state.weather_client = None
        finally:
            app.state.session_factory = None
            await engine.dispose()

    app = FastAPI(title="Garden Service", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Nonstandard JSON NaN/Infinity inputs still get a serializable 422.
        errors = jsonable_encoder(exc.errors(), custom_encoder={
            float: lambda value: value if math.isfinite(value) else str(value),
        })
        return JSONResponse(status_code=422, content={"detail": errors})

    async def database_unavailable(request, exc):
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})

    app.add_exception_handler(DBAPIError, database_unavailable)
    app.add_exception_handler(DatabaseTimeout, database_unavailable)

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready(session: Session):
        await check_schema(session)
        return {"status": "ok"}

    @app.post("/plants", response_model=PlantResponse, status_code=201)
    async def create(data: PlantCreate, session: Session):
        return await crud.create_plant(session, data)

    async def enrich(request: Request, plants):
        return await enrich_plants(
            plants, getattr(request.app.state, "weather_client", None),
            max_concurrency=getattr(request.app.state, "weather_max_concurrency", 5),
            timeout_seconds=getattr(request.app.state, "weather_enrichment_timeout_seconds", 45),
        )

    @app.get("/plants", response_model=list[EnrichedPlantResponse])
    async def list_all(request: Request, session: Session):
        return await enrich(request, await crud.list_plants(session))

    @app.get("/plants/{plant_id}", response_model=EnrichedPlantResponse)
    async def get(plant_id: int, request: Request, session: Session):
        plant = await crud.get_plant(session, plant_id)
        if plant is None:
            raise HTTPException(404, "Plant not found")
        return (await enrich(request, [plant]))[0]

    @app.patch("/plants/{plant_id}", response_model=PlantResponse)
    async def patch(plant_id: int, data: PlantPatch, session: Session):
        plant = await crud.update_plant(session, plant_id, data)
        if plant is None:
            raise HTTPException(404, "Plant not found")
        return plant

    @app.delete("/plants/{plant_id}", status_code=204, response_class=Response)
    async def delete(plant_id: int, session: Session):
        if not await crud.delete_plant(session, plant_id):
            raise HTTPException(404, "Plant not found")
        return Response(status_code=204)

    return app


app = create_app()
