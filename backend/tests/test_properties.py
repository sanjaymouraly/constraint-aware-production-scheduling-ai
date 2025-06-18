"""Property based tests (Hypothesis).

These tests explore the *input space* of the scheduling engine and assert
invariants that must hold for **every** generated instance, not just for the
hand written demo data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

from app.domain.calendars import (
    Availability,
    BreakSpec,
    CalendarEngine,
    CalendarSpec,
    ExceptionSpec,
    ShiftSpec,
)
from app.domain.timeutils import Interval, add_minutes, merge_intervals, subtract_intervals
from app.scheduling.metrics import compare_metrics
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

UTC = UTC
ENGINE = CalendarEngine()

SETTINGS = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

shift_starts = st.integers(min_value=0, max_value=22 * 60)
shift_lengths = st.integers(min_value=30, max_value=16 * 60)
durations = st.integers(min_value=1, max_value=24 * 60)
instants = st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2030, 1, 1))


@SETTINGS
@given(
    start=shift_starts,
    length=shift_lengths,
)
def test_shift_never_exceeds_a_day(start: int, length: int) -> None:
    """A shift may spill into the next day but never beyond 48 hours."""
    shift = ShiftSpec(day_of_week=1, start_minute=start, end_minute=start + length)
    assert shift.length_minutes == length
    assert 0 <= shift.start_minute <= 24 * 60
    assert shift.end_minute <= 48 * 60


def _union_minutes(intervals: list[Interval]) -> int:
    """Reference implementation of the length of a union of intervals."""
    total = 0
    current_start = None
    current_end = None
    for interval in sorted(intervals, key=lambda i: (i.start, i.end)):
        if current_end is None or interval.start > current_end:
            if current_start is not None and current_end is not None:
                total += int(round((current_end - current_start).total_seconds() / 60.0))
            current_start, current_end = interval.start, interval.end
        else:
            current_end = max(current_end, interval.end)
    if current_start is not None and current_end is not None:
        total += int(round((current_end - current_start).total_seconds() / 60.0))
    return total


@SETTINGS
@given(intervals=st.lists(st.tuples(instants, durations), min_size=1, max_size=8))
def test_merged_intervals_never_lose_time(intervals) -> None:
    """Merging preserves the covered time and never creates overlaps."""
    converted = [Interval(start, add_minutes(start, duration)) for start, duration in intervals]
    merged = merge_intervals(converted)
    assert sum(segment.minutes for segment in merged) == _union_minutes(converted)
    for previous, current in pairwise(merged):
        assert previous.end <= current.start


@SETTINGS
@given(
    base_start=instants,
    base_length=durations,
    hole_start=st.integers(min_value=0, max_value=24 * 60),
    hole_length=durations,
)
def test_subtracting_a_hole_never_adds_time(
    base_start: datetime, base_length: int, hole_start: int, hole_length: int
) -> None:
    base = Interval(base_start, add_minutes(base_start, base_length))
    hole = Interval(
        add_minutes(base_start, hole_start), add_minutes(base_start, hole_start + hole_length)
    )
    remaining = subtract_intervals([base], [hole])
    assert sum(segment.minutes for segment in remaining) <= base.minutes
    assert all(segment.minutes >= 0 for segment in remaining)


@SETTINGS
@given(
    day_start=st.integers(min_value=0, max_value=20 * 60),
    day_length=durations,
    duration=durations,
)
def test_earliest_start_is_never_before_the_lower_bound(
    day_start: int, day_length: int, duration: int
) -> None:
    """``earliest_start`` honours the requested lower bound and the shift."""
    spec = CalendarSpec(
        id=1,
        code="PROP",
        timezone="UTC",
        shifts=(
            ShiftSpec(day_of_week=1, start_minute=day_start, end_minute=day_start + day_length),
        ),
    )
    availability = ENGINE.availability(
        spec, datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 1, 12, tzinfo=UTC)
    )
    lower = datetime(2026, 1, 5, tzinfo=UTC) + timedelta(hours=2)
    start = availability.earliest_start(duration, not_before=lower)
    if start is None:
        return
    assert start >= lower
    assert availability.contains(start, add_minutes(start, duration))


@SETTINGS
@given(duration=durations)
def test_continuous_availability_covers_the_whole_horizon(duration: int) -> None:
    spec = CalendarSpec(id=2, code="PROP-24X7", timezone="UTC", is_continuous=True)
    availability = ENGINE.availability(
        spec, datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 1, 6, tzinfo=UTC)
    )
    assert availability.total_working_minutes == 24 * 60
    start = availability.earliest_start(duration)
    assume(start is not None)
    assert availability.contains(start, add_minutes(start, duration))


@SETTINGS
@given(
    breaks=st.lists(
        st.tuples(st.integers(min_value=0, max_value=6), st.integers(min_value=0, max_value=1400)),
        max_size=4,
    )
)
def test_breaks_only_reduce_availability(breaks) -> None:
    """Adding breaks can never increase the available working time."""
    spec = CalendarSpec(
        id=3,
        code="PROP-BREAK",
        timezone="UTC",
        shifts=(ShiftSpec(day_of_week=1, start_minute=360, end_minute=1080),),
    )
    base = ENGINE.availability(
        spec, datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 1, 12, tzinfo=UTC)
    )
    with_breaks = CalendarSpec(
        id=3,
        code="PROP-BREAK",
        timezone="UTC",
        shifts=(ShiftSpec(day_of_week=1, start_minute=360, end_minute=1080),),
        breaks=tuple(
            BreakSpec(day_of_week=day, start_minute=0, end_minute=end) for day, end in breaks
        ),
    )
    reduced = ENGINE.availability(
        with_breaks, datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 1, 12, tzinfo=UTC)
    )
    assert reduced.total_working_minutes <= base.total_working_minutes


@SETTINGS
@given(
    baseline=st.fixed_dictionaries(
        {
            "on_time_delivery_pct": st.floats(min_value=0.0, max_value=100.0),
            "weighted_tardiness": st.floats(min_value=0.0, max_value=100000.0),
            "makespan_minutes": st.floats(min_value=1.0, max_value=100000.0),
        }
    ),
    factor=st.floats(min_value=0.0, max_value=1.0),
)
def test_an_identical_plan_is_never_better(baseline: dict, factor: float) -> None:
    """Comparing a plan with itself must never report an improvement."""
    optimised = dict(baseline)
    comparison = compare_metrics(baseline, optimised)
    assert comparison["improved_metrics"] == 0
    assert comparison["regressed_metrics"] == 0
    for row in comparison["rows"]:
        assert row["verdict"] == "equal"


@SETTINGS
@given(
    tardiness=st.floats(min_value=0.0, max_value=10000.0),
)
def test_less_tardiness_is_always_better(tardiness: float) -> None:
    baseline = {
        "on_time_delivery_pct": 80.0,
        "weighted_tardiness": tardiness + 100.0,
        "makespan_minutes": 1000.0,
    }
    optimised = {
        "on_time_delivery_pct": 80.0,
        "weighted_tardiness": tardiness,
        "makespan_minutes": 1000.0,
    }
    comparison = compare_metrics(baseline, optimised)
    row = next(r for r in comparison["rows"] if r["metric"] == "weighted_tardiness")
    assert row["verdict"] == "better"
    assert row["delta"] <= 0


@SETTINGS
@given(
    day=st.integers(min_value=1, max_value=28),
    window_start=st.integers(min_value=0, max_value=1300),
    window_length=st.integers(min_value=10, max_value=140),
)
def test_windowed_exceptions_remove_exactly_their_window(
    day: int, window_start: int, window_length: int
) -> None:
    """A windowed shutdown removes that window and nothing else."""
    from datetime import date

    exception_day = date(2026, 1, day)
    spec = CalendarSpec(
        id=4,
        code="PROP-EXC",
        timezone="UTC",
        shifts=(
            ShiftSpec(day_of_week=exception_day.isoweekday(), start_minute=0, end_minute=1440),
        ),
        exceptions=(
            ExceptionSpec(
                day=exception_day,
                kind="shutdown",
                start_minute=window_start,
                end_minute=window_start + window_length,
            ),
        ),
    )
    availability = ENGINE.availability(
        spec, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
    )
    baseline = CalendarSpec(
        id=4,
        code="PROP-EXC",
        timezone="UTC",
        shifts=(
            ShiftSpec(day_of_week=exception_day.isoweekday(), start_minute=0, end_minute=1440),
        ),
    )
    plain = ENGINE.availability(
        baseline, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
    )
    removed = plain.total_working_minutes - availability.total_working_minutes
    assert removed == window_length


@SETTINGS
@given(duration=st.integers(min_value=1, max_value=12 * 60))
def test_availability_helpers_agree(duration: int) -> None:
    """``contains`` and ``overlaps_working`` must be consistent."""
    spec = CalendarSpec(id=5, code="PROP-AGREE", timezone="UTC", is_continuous=True)
    availability: Availability = ENGINE.availability(
        spec, datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 1, 6, tzinfo=UTC)
    )
    start = datetime(2026, 1, 5, tzinfo=UTC)
    end = add_minutes(start, duration)
    assert availability.contains(start, end)
    assert availability.overlaps_working(start, end)
    assert availability.working_minutes_between(start, end) == duration
