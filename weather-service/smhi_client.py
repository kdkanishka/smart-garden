"""Asynchronous adapter for the fixed SMHI snow1g point-forecast endpoint."""

import asyncio

import httpx


class UpstreamError(Exception):
    """SMHI could not provide a usable response."""


class UpstreamTimeout(UpstreamError):
    """The overall SMHI request deadline was exceeded."""


class SMHIClient:
    def __init__(self, client: httpx.AsyncClient, timeout_seconds: float):
        self.client = client
        self.timeout_seconds = timeout_seconds

    async def fetch(self, lat: float, lon: float) -> object:
        # Fixed-point formatting also prevents scientific notation near zero.
        url = (
            "https://opendata-download-metfcst.smhi.se/api/category/snow1g/"
            f"version/1/geotype/point/lon/{lon:.6f}/lat/{lat:.6f}/data.json"
        )
        try:
            # Unlike per-operation HTTP timeouts, this includes all response reads.
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.client.get(url)
                response.raise_for_status()
                return response.json()
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise UpstreamTimeout("SMHI request deadline exceeded") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise UpstreamError("Unusable SMHI response or unsupported location") from exc
