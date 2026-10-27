"""Pure snow1g precipitation calculations (amounts are mm water equivalent).

All times are supplied by the caller; this module performs no I/O and reads no
clock or configuration. Boundary proration assumes uniform precipitation within
each explicit upstream interval.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import fsum, isfinite


class ForecastError(ValueError):
    """The upstream payload cannot provide a usable, complete forecast."""


@dataclass(frozen=True)
class _Interval:
    start: datetime
    end: datetime
    amount_mm: float


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ForecastError(f"Missing or invalid {field}")
    try:
        return _utc(datetime.fromisoformat(value), field)
    except ValueError as exc:
        raise ForecastError(f"Missing or invalid {field}") from exc


def _number(value: object, field: str) -> float:
    # JSON booleans and numeric strings are not precipitation numbers.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite nonnegative number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{field} must be a finite nonnegative number") from exc
    if not isfinite(number) or number < 0:
        raise ValueError(f"{field} must be a finite nonnegative number")
    return number


def _intervals(
    entries: list, window_start: datetime, window_end: datetime
) -> list[_Interval]:
    intervals = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ForecastError("Each timeSeries entry must be an object")
        start = _timestamp(
            entry.get("intervalParametersStartTime"), "intervalParametersStartTime"
        )
        end = _timestamp(entry.get("time"), "time")

        # Ignore wholly outside entries, including their unused precipitation.
        # Both endpoints must be outside on the same side, so a reversed
        # interval spanning the window cannot evade duration validation.
        if max(start, end) <= window_start or min(start, end) >= window_end:
            continue
        if end <= start:
            raise ForecastError("Precipitation interval duration must be positive")
        data = entry.get("data")
        if not isinstance(data, Mapping):
            raise ForecastError("Missing or invalid interval data")
        try:
            amount = _number(
                data.get("precipitation_amount_mean"), "precipitation_amount_mean"
            )
        except ValueError as exc:
            raise ForecastError(str(exc)) from exc
        if amount == 9999:
            raise ForecastError("precipitation_amount_mean is missing (9999)")
        intervals.append(_Interval(start, end, amount))
    return sorted(intervals, key=lambda interval: interval.start)


def calculate_forecast(
    payload: object,
    *,
    window_start: datetime,
    calculated_at: datetime,
    threshold_mm: float = 5.0,
) -> dict[str, float | str]:
    """Return the weather object for a complete, nonoverlapping 48-hour window.

    ``payload`` is decoded snow1g JSON. Invalid upstream data raises
    ``ForecastError``; invalid caller times or threshold raise ``ValueError``.
    Entries wholly outside the half-open window are ignored after reading their
    bounds. Unreadable bounds are rejected because overlap cannot be determined.
    Advice compares the full-precision sum; only the response amount is rounded.
    """
    window_start = _utc(window_start, "window_start")
    calculated_at = _utc(calculated_at, "calculated_at")
    threshold = _number(threshold_mm, "threshold_mm")
    window_end = window_start + timedelta(hours=48)

    if not isinstance(payload, Mapping):
        raise ForecastError("Forecast payload must be an object")
    reference_time = _timestamp(payload.get("referenceTime"), "referenceTime")
    entries = payload.get("timeSeries")
    if not isinstance(entries, list):
        raise ForecastError("Missing or invalid timeSeries")

    cursor = window_start
    contributions = []
    for interval in _intervals(entries, window_start, window_end):
        start = max(interval.start, window_start)
        end = min(interval.end, window_end)
        if start > cursor:
            raise ForecastError("Gap in precipitation coverage")
        if start < cursor:
            raise ForecastError("Overlapping precipitation intervals")
        fraction = (end - start) / (interval.end - interval.start)
        contributions.append(interval.amount_mm * fraction)
        cursor = end

    if cursor != window_end:
        raise ForecastError("Incomplete 48-hour precipitation coverage")
    try:
        total = fsum(contributions)
    except OverflowError as exc:
        raise ForecastError("Nonfinite total precipitation") from exc
    if not isfinite(total):
        raise ForecastError("Nonfinite total precipitation")

    def iso(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    return {
        "rain_expected_mm": round(total, 1),
        "advice": "Skip watering" if total >= threshold else "Consider watering",
        "window_start": iso(window_start),
        "window_end": iso(window_end),
        "forecast_reference_time": iso(reference_time),
        "calculated_at": iso(calculated_at),
    }
