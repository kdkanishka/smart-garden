from datetime import datetime, timedelta, timezone
from decimal import Decimal

from pydantic import ValidationError
import pytest

from schemas import PlantCreate, PlantPatch, PlantResponse


@pytest.mark.parametrize("schema", [PlantCreate, PlantPatch])
@pytest.mark.parametrize("field,value", [
    ("name", ""), ("name", " \t\n"), ("name", "x" * 101), ("name", 123),
    ("name", None), ("lat", None), ("lon", None),
    ("lat", "word"), ("lon", []), ("lat", True), ("lon", False),
    ("lat", "NaN"), ("lon", "Infinity"), ("lat", float("nan")),
    ("lon", float("inf")), ("lat", "-Infinity"),
    ("lat", "90.00000001"), ("lat", "-90.00000001"),
    ("lon", "180.00000001"), ("lon", "-180.00000001"),
])
def test_invalid_fields(schema, field, value):
    with pytest.raises(ValidationError):
        schema(**({"name": "Mint", "lat": 0, "lon": 0} | {field: value}))


@pytest.mark.parametrize("missing", ["name", "lat", "lon"])
def test_post_requires_all_fields(missing):
    data = {"name": "Mint", "lat": 0, "lon": 0}
    del data[missing]
    with pytest.raises(ValidationError):
        PlantCreate(**data)


def test_patch_requires_nonempty_known_fields():
    for data in [{}, {"unknown": 1}, {"name": "Mint", "id": 1}]:
        with pytest.raises(ValidationError):
            PlantPatch(**data)
    with pytest.raises(ValidationError):
        PlantCreate(name="Mint", lat=0, lon=0, weather={})


@pytest.mark.parametrize("data", [
    {"name": "  Mint\n"}, {"lat": 90}, {"lon": -180},
    {"name": "Rose", "lat": -90}, {"lat": 0, "lon": 180},
    {"name": "Rose", "lat": 1, "lon": 2},
])
def test_partial_patch_preserves_only_supplied_fields(data):
    patch = PlantPatch(**data).model_dump(exclude_unset=True)
    assert patch.keys() == data.keys()
    if "name" in patch:
        assert patch["name"] == data["name"].strip()


def test_trim_before_length_check_and_normalize_coordinates():
    plant = PlantCreate(name=" \t" + "x" * 100 + "\n", lat="57.1234568", lon="-0.00000001")
    assert plant.name == "x" * 100
    assert plant.lat == Decimal("57.123457")
    assert plant.lon == Decimal("0.000000")
    assert not plant.lon.is_signed()


def test_response_has_numeric_coordinates_and_utc_z_timestamps():
    timestamp = datetime(2026, 10, 27, 14, 30, tzinfo=timezone(timedelta(hours=2)))
    response = PlantResponse(
        id=1, name="Mint", lat=Decimal("57.700000"), lon=Decimal("11.900000"),
        created_at=timestamp, updated_at=timestamp,
    ).model_dump(mode="json")
    assert response == {
        "id": 1, "name": "Mint", "lat": 57.7, "lon": 11.9,
        "created_at": "2026-10-27T12:30:00Z", "updated_at": "2026-10-27T12:30:00Z",
    }
