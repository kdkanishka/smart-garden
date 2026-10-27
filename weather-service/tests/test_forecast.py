import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from forecast import ForecastError, calculate_forecast


START = datetime(2026, 10, 27, 9, tzinfo=timezone.utc)
END = START + timedelta(hours=48)
CALCULATED = START + timedelta(seconds=1)


def interval(start, end, amount):
    return {
        "intervalParametersStartTime": start.isoformat(),
        "time": end.isoformat(),
        "data": {"precipitation_amount_mean": amount},
    }


def payload(*entries):
    return {"referenceTime": "2026-10-27T06:00:00Z", "timeSeries": list(entries)}


def calculate(data, **kwargs):
    return calculate_forecast(
        data, window_start=START, calculated_at=CALCULATED, **kwargs
    )


@pytest.fixture
def representative():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "snow1g.json").read_text()
    )


def test_representative_fields_and_both_partial_boundaries(representative):
    # 3/6 * 1.2 + 2.4 + 4.8 + 9/12 * 2.4 = 9.6 mm.
    assert calculate(representative) == {
        "rain_expected_mm": 9.6,
        "advice": "Skip watering",
        "window_start": "2026-10-27T09:00:00Z",
        "window_end": "2026-10-29T09:00:00Z",
        "forecast_reference_time": "2026-10-27T06:00:00Z",
        "calculated_at": "2026-10-27T09:00:01Z",
    }


def test_48_hourly_intervals_sorted_and_summed():
    entries = [
        interval(START + timedelta(hours=i), START + timedelta(hours=i + 1), 0.1)
        for i in range(48)
    ]
    assert calculate(payload(*reversed(entries)))["rain_expected_mm"] == 4.8


def test_nonhourly_values_are_interval_amounts_not_rates():
    assert calculate(payload(
        interval(START, START + timedelta(hours=6), 1.0),
        interval(START + timedelta(hours=6), END, 2.0),
    ))["rain_expected_mm"] == 3.0


def test_one_interval_encloses_entire_window():
    assert calculate(payload(interval(
        START - timedelta(hours=24), END + timedelta(hours=24), 12
    )))["rain_expected_mm"] == 6.0


def test_subsecond_boundary_proration():
    assert calculate(payload(interval(
        START - timedelta(microseconds=500000), END + timedelta(microseconds=500000),
        172801,
    )))["rain_expected_mm"] == 172800.0


def test_utc_normalization_across_month_year_and_offsets():
    start = datetime.fromisoformat("2026-12-31T23:30:00+02:00")
    end = start + timedelta(hours=48)
    data = payload(interval(start, end, 2))
    data["referenceTime"] = "2026-12-31T18:00:00-03:00"
    result = calculate_forecast(data, window_start=start, calculated_at=start)
    assert result["window_start"] == "2026-12-31T21:30:00Z"
    assert result["window_end"] == "2027-01-02T21:30:00Z"
    assert result["forecast_reference_time"] == "2026-12-31T21:00:00Z"
    assert result["calculated_at"] == result["window_start"]


@pytest.mark.parametrize("amount,display,advice", [
    (0, 0.0, "Consider watering"),
    (4.9, 4.9, "Consider watering"),
    (4.96, 5.0, "Consider watering"),
    (5, 5.0, "Skip watering"),
    (5.04, 5.0, "Skip watering"),
    (5.1, 5.1, "Skip watering"),
])
def test_threshold_uses_unrounded_total(amount, display, advice):
    result = calculate(payload(interval(START, END, amount)))
    assert result["rain_expected_mm"] == display
    assert result["advice"] == advice


def test_no_intermediate_rounding_and_stable_sum():
    entries = [
        interval(START + timedelta(hours=i), START + timedelta(hours=i + 1), 0.104)
        for i in range(48)
    ]
    result = calculate(payload(*entries))
    assert result["rain_expected_mm"] == 5.0  # 4.992, not 4.8
    assert result["advice"] == "Consider watering"
    for entry in entries:
        entry["data"]["precipitation_amount_mean"] = 0.1
    assert calculate(payload(*entries), threshold_mm=4.8)["advice"] == "Skip watering"


def test_configurable_threshold():
    data = payload(interval(START, END, 3))
    assert calculate(data, threshold_mm=3)["advice"] == "Skip watering"
    assert calculate(data, threshold_mm=4)["advice"] == "Consider watering"
    assert calculate(payload(interval(START, END, 0)), threshold_mm=0)["advice"] == "Skip watering"


@pytest.mark.parametrize("amount", [
    None, "1.5", "NaN", True, False, [], {}, -0.01,
    float("nan"), float("inf"), float("-inf"), 9999, 9999.0, 10**400,
])
def test_reject_invalid_precipitation(amount):
    with pytest.raises(ForecastError):
        calculate(payload(interval(START, END, amount)))


@pytest.mark.parametrize("field", ["data", "precipitation_amount_mean"])
def test_missing_precipitation_field(field):
    entry = interval(START, END, 1)
    del (entry if field == "data" else entry["data"])[field]
    with pytest.raises(ForecastError):
        calculate(payload(entry))


@pytest.mark.parametrize("data", [None, [], "invalid", 1])
def test_invalid_interval_data(data):
    entry = interval(START, END, 1)
    entry["data"] = data
    with pytest.raises(ForecastError):
        calculate(payload(entry))


@pytest.mark.parametrize("field", ["intervalParametersStartTime", "time", "referenceTime"])
@pytest.mark.parametrize("value", [None, "not-a-date", "2026-10-27T09:00:00", 123])
def test_invalid_or_missing_timestamp(field, value):
    data = payload(interval(START, END, 1))
    target = data if field == "referenceTime" else data["timeSeries"][0]
    target[field] = value
    with pytest.raises(ForecastError):
        calculate(data)
    del target[field]
    with pytest.raises(ForecastError):
        calculate(data)


@pytest.mark.parametrize("data", [
    None, [], "{}", {}, {"referenceTime": "2026-10-27T06:00:00Z"},
    {"referenceTime": "2026-10-27T06:00:00Z", "timeSeries": None},
    {"referenceTime": "2026-10-27T06:00:00Z", "timeSeries": {}},
    payload(None), payload("invalid"), payload([]), payload(),
])
def test_malformed_payload_or_no_coverage(data):
    with pytest.raises(ForecastError):
        calculate(data)


@pytest.mark.parametrize("first_end,second_start", [
    (24, 25), (25, 24), (24, 24.00000001), (24.00000001, 24),
])
def test_internal_gaps_and_overlaps(first_end, second_start):
    with pytest.raises(ForecastError, match="Gap|Overlapping"):
        calculate(payload(
            interval(START, START + timedelta(hours=first_end), 1),
            interval(START + timedelta(hours=second_start), END, 1),
        ))


@pytest.mark.parametrize("start,end", [
    (START + timedelta(microseconds=1), END),
    (START, END - timedelta(microseconds=1)),
    (START - timedelta(days=4), START),
    (END, END + timedelta(days=4)),
])
def test_insufficient_coverage(start, end):
    with pytest.raises(ForecastError):
        calculate(payload(interval(start, end, 0)))


@pytest.mark.parametrize("extra", [
    interval(START, END, 1),
    interval(START - timedelta(hours=1), START + timedelta(hours=1), 1),
    interval(END - timedelta(hours=1), END + timedelta(hours=1), 1),
    interval(START + timedelta(hours=1), END - timedelta(hours=1), 1),
])
def test_duplicate_nested_and_boundary_overlaps(extra):
    with pytest.raises(ForecastError, match="Overlapping"):
        calculate(payload(interval(START, END, 1), extra))


@pytest.mark.parametrize("start,end", [
    (START + timedelta(hours=1), START + timedelta(hours=1)),
    (END, START),
    (END + timedelta(hours=1), START - timedelta(hours=1)),
])
def test_nonpositive_durations(start, end):
    with pytest.raises(ForecastError, match="duration"):
        calculate(payload(interval(START, END, 1), interval(start, end, 0)))


def test_ignore_wholly_outside_entries_even_invalid_amounts():
    before = interval(START - timedelta(hours=3), START, 9999)
    after = interval(END, END + timedelta(hours=3), float("nan"))
    del before["data"]
    assert calculate(payload(before, interval(START, END, 1), after))[
        "rain_expected_mm"
    ] == 1.0


def test_unreadable_bounds_cannot_be_ignored():
    with pytest.raises(ForecastError):
        calculate(payload(interval(START, END, 1), {"time": "invalid"}))


def test_finite_amounts_with_overflowing_total_rejected():
    middle = START + timedelta(hours=24)
    with pytest.raises(ForecastError, match="Nonfinite total"):
        calculate(payload(interval(START, middle, 1e308), interval(middle, END, 1e308)))


def test_large_finite_amount_proration_does_not_overflow():
    assert calculate(payload(interval(START, END, 1e308)))["rain_expected_mm"] == 1e308


@pytest.mark.parametrize("threshold", [-1, None, "5", True, float("nan"), float("inf")])
def test_invalid_threshold(threshold):
    with pytest.raises(ValueError):
        calculate(payload(interval(START, END, 1)), threshold_mm=threshold)


@pytest.mark.parametrize("field", ["window_start", "calculated_at"])
@pytest.mark.parametrize("value", [None, "2026-10-27T09:00:00Z", START.replace(tzinfo=None)])
def test_caller_must_supply_aware_times(field, value):
    kwargs = {"window_start": START, "calculated_at": CALCULATED, field: value}
    with pytest.raises(ValueError):
        calculate_forecast(payload(interval(START, END, 1)), **kwargs)


def test_calculation_does_not_mutate_payload(representative):
    original = json.dumps(representative)
    assert calculate(representative) == calculate(representative)
    assert json.dumps(representative) == original
