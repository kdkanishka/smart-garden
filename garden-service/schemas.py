"""Plant API validation and JSON serialization."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field,
    StringConstraints, field_serializer, field_validator, model_validator,
)


def reject_boolean(value):
    if isinstance(value, bool):
        raise ValueError("Coordinates must be finite numbers")
    return value


def round_coordinate(value: Decimal) -> Decimal:
    # Bounds are validated before rounding so just-outside values stay invalid.
    rounded = round(value, 6)
    return rounded if rounded else Decimal("0.000000")


Name = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)]
Latitude = Annotated[
    Decimal, BeforeValidator(reject_boolean),
    Field(ge=-90, le=90, allow_inf_nan=False), AfterValidator(round_coordinate),
]
Longitude = Annotated[
    Decimal, BeforeValidator(reject_boolean),
    Field(ge=-180, le=180, allow_inf_nan=False), AfterValidator(round_coordinate),
]


class PlantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    lat: Latitude
    lon: Longitude


class PlantPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    lat: Latitude | None = None
    lon: Longitude | None = None

    @field_validator("name", "lat", "lon", mode="before")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Field must not be null")
        return value

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set:
            raise ValueError("At least one plant field is required")
        return self


class PlantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    lat: Decimal
    lon: Decimal
    created_at: datetime
    updated_at: datetime

    @field_serializer("lat", "lon", when_used="json")
    def serialize_coordinate(self, value: Decimal) -> float:
        return float(value)

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Plant timestamps must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def require_iso_timestamp(value):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("Weather timestamps must be ISO 8601 strings with a timezone")
    return value


WeatherTimestamp = Annotated[AwareDatetime, BeforeValidator(require_iso_timestamp)]


class WeatherResponse(BaseModel):
    """Validate the Analyzer's data before exposing it as available weather."""

    rain_expected_mm: float = Field(strict=True, ge=0, allow_inf_nan=False)
    advice: Literal["Skip watering", "Consider watering"]
    window_start: WeatherTimestamp
    window_end: WeatherTimestamp
    forecast_reference_time: WeatherTimestamp
    calculated_at: WeatherTimestamp

    @model_validator(mode="after")
    def require_forecast_window(self):
        if self.window_end - self.window_start != timedelta(hours=48):
            raise ValueError("Weather must cover a 48-hour window")
        return self

    @field_serializer(
        "window_start", "window_end", "forecast_reference_time", "calculated_at",
        when_used="json",
    )
    def serialize_timestamp(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class EnrichedPlantResponse(PlantResponse):
    weather_status: Literal["available", "unavailable"]
    weather: WeatherResponse | None
