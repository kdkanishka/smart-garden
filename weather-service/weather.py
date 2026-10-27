"""Per-process forecast cache and coalesced asynchronous calculations."""

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime

from forecast import ForecastError, calculate_forecast
from smhi_client import SMHIClient, UpstreamError


class WeatherAnalyzer:
    def __init__(
        self,
        smhi: SMHIClient,
        *,
        threshold_mm: float,
        ttl_seconds: float,
        max_entries: int,
        utc_now: Callable[[], datetime],
        monotonic: Callable[[], float],
    ):
        self.smhi = smhi
        self.threshold_mm = threshold_mm
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.utc_now = utc_now
        self.monotonic = monotonic
        self.cache = OrderedDict()
        self.inflight = {}

    async def get(self, lat: float, lon: float) -> dict:
        key = (lat, lon)
        cached = self.cache.get(key)
        if cached is not None:
            expires_at, result = cached
            if self.monotonic() < expires_at:
                self.cache.move_to_end(key)
                return result
            del self.cache[key]

        # There is no await between checking and registering a miss, so all
        # requests on this event loop share the same calculation for this key.
        task = self.inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._calculate(key))
            self.inflight[key] = task
            task.add_done_callback(lambda completed: self._finished(key, completed))
        # One disconnected caller must not cancel other callers' shared work.
        return await asyncio.shield(task)

    def _finished(self, key: tuple, task: asyncio.Task) -> None:
        self.inflight.pop(key, None)
        # Retrieve failures even if every waiting caller has disconnected.
        if not task.cancelled():
            task.exception()

    async def _calculate(self, key: tuple) -> dict:
        window_start = self.utc_now()
        payload = await self.smhi.fetch(*key)
        try:
            result = calculate_forecast(
                payload,
                window_start=window_start,
                calculated_at=self.utc_now(),
                threshold_mm=self.threshold_mm,
            )
        except ForecastError as exc:
            raise UpstreamError("Incomplete or invalid SMHI forecast") from exc
        self.cache[key] = (self.monotonic() + self.ttl_seconds, result)
        self.cache.move_to_end(key)
        while len(self.cache) > self.max_entries:
            self.cache.popitem(last=False)
        return result

    async def close(self) -> None:
        tasks = list(self.inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.inflight.clear()
        self.cache.clear()
