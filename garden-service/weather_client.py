"""Weather Analyzer adapter and request-local, deadline-bounded enrichment."""

import asyncio

import httpx

from schemas import EnrichedPlantResponse, PlantResponse, WeatherResponse, round_coordinate


class WeatherClient:
    def __init__(self, client: httpx.AsyncClient, service_url: str, timeout_seconds: float = 12):
        self.client = client
        self.url = service_url.rstrip("/") + "/api/weather"
        self.timeout_seconds = timeout_seconds

    async def fetch(self, lat, lon) -> WeatherResponse | None:
        try:
            # One deadline covers connection, headers, and all response-body reads.
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.client.get(
                    self.url, params={"lat": f"{lat:.6f}", "lon": f"{lon:.6f}"},
                )
                response.raise_for_status()
                return WeatherResponse.model_validate(response.json())
        except (TimeoutError, httpx.HTTPError, ValueError):
            return None


async def enrich_plants(
    plants, weather_client: WeatherClient | None, *, max_concurrency: int = 5,
    timeout_seconds: float = 45,
) -> list[EnrichedPlantResponse]:
    stored = [PlantResponse.model_validate(plant) for plant in plants]
    keys = [(round_coordinate(plant.lat), round_coordinate(plant.lon)) for plant in stored]
    results = dict.fromkeys(keys)

    if weather_client is not None and results:
        # A fixed worker pool bounds active lookups and avoids one task per plant.
        coordinates = iter(results)

        async def worker():
            for lat, lon in coordinates:
                results[(lat, lon)] = await weather_client.fetch(lat, lon)

        tasks = [asyncio.create_task(worker()) for _ in range(min(max_concurrency, 5, len(results)))]
        try:
            async with asyncio.timeout(timeout_seconds):
                await asyncio.gather(*tasks)
        except TimeoutError:
            # Completed results survive; unfinished and unstarted pairs stay null.
            pass
        finally:
            # Also clean up workers if the Garden request itself is cancelled.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    return [
        EnrichedPlantResponse(
            **plant.model_dump(),
            weather_status="available" if results[key] is not None else "unavailable",
            weather=results[key],
        )
        for plant, key in zip(stored, keys)
    ]
