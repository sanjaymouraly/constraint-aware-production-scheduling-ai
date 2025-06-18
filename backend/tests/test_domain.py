"""Unit tests for the domain layer (time utilities and calendar engine)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from app.domain.calendars import (
    Availability,
    BreakSpec,
    CalendarEngine,
    CalendarSpec,
    ExceptionSpec,
    ShiftSpec,
)
from app.domain.timeutils import (
    Interval,
    add_minutes,
    ensure_utc,
    intersect_intervals,
    merge_intervals,
    minutes_between,
    subtract_intervals,
)
from app.scheduling.builder import _duration_for

UTC = UTC
ENGINE = CalendarEngine()


def _availability(spec: CalendarSpec, start: date, end: date) -> Availability:
    return ENGINE.availability(
        spec,
        datetime(start.year, start.month, start.day, tzinfo=UTC),
        datetime(end.year, end.month, end.day, tzinfo=UTC),
    )


class TestTimeUtils:
    def test_ensure_utc_attaches_timezone(self) -> None:
        naive = datetime(2026, 1, 1, 10, 0)
        assert ensure_utc(naive).tzinfo is UTC

    def test_ensure_utc_converts(self) -> None:
        aware = datetime(2026, 1, 1, 10, 0, tzinfo=timezone(timedelta(hours=5)))
        assert ensure_utc(aware).hour == 5

    def test_add_minutes(self) -> None:
        start = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        assert add_minutes(start, 90) == start + timedelta(minutes=90)
        assert add_minutes(start, 1.5) == start + timedelta(seconds=90)

    def test_minutes_between(self) -> None:
        a = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        b = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)
        assert minutes_between(a, b) == 150

    def test_interval_overlap_is_half_open(self) -> None:
        first = Interval(datetime(2026, 1, 1, 10, tzinfo=UTC), datetime(2026, 1, 1, 11, tzinfo=UTC))
        touching = Interval(
            datetime(2026, 1, 1, 11, tzinfo=UTC), datetime(2026, 1, 1, 12, tzinfo=UTC)
        )
        overlapping = Interval(
            datetime(2026, 1, 1, 10, 59, tzinfo=UTC), datetime(2026, 1, 1, 12, tzinfo=UTC)
        )
        assert not first.overlaps(touching)
        assert first.overlaps(overlapping)

    def test_interval_contains(self) -> None:
        outer = Interval(datetime(2026, 1, 1, 10, tzinfo=UTC), datetime(2026, 1, 1, 12, tzinfo=UTC))
        inner = Interval(
            datetime(2026, 1, 1, 10, 30, tzinfo=UTC), datetime(2026, 1, 1, 11, tzinfo=UTC)
        )
        assert outer.contains(inner)
        assert not inner.contains(outer)

    def test_merge_intervals_merges_adjacent(self) -> None:
        base = datetime(2026, 1, 1, 10, tzinfo=UTC)
        merged = merge_intervals(
            [
                Interval(base, base + timedelta(minutes=60)),
                Interval(base + timedelta(minutes=60), base + timedelta(minutes=120)),
            ]
        )
        assert len(merged) == 1
        assert merged[0].minutes == 120

    def test_subtract_intervals_cuts_a_hole(self) -> None:
        base = datetime(2026, 1, 1, 10, tzinfo=UTC)
        result = subtract_intervals(
            [Interval(base, base + timedelta(minutes=120))],
            [Interval(base + timedelta(minutes=30), base + timedelta(minutes=60))],
        )
        assert len(result) == 2
        assert sum(segment.minutes for segment in result) == 90

    def test_intersect_intervals(self) -> None:
        a = Interval(datetime(2026, 1, 1, 10, tzinfo=UTC), datetime(2026, 1, 1, 12, tzinfo=UTC))
        b = Interval(datetime(2026, 1, 1, 11, tzinfo=UTC), datetime(2026, 1, 1, 13, tzinfo=UTC))
        result = intersect_intervals([a], [b])
        assert len(result) == 1
        assert result[0].minutes == 60


class TestCalendarEngine:
    def test_continuous_calendar_has_no_gaps(self) -> None:
        spec = CalendarSpec(id=1, code="TEST-24X7", timezone="UTC", is_continuous=True)
        availability = _availability(spec, date(2026, 1, 5), date(2026, 1, 6))
        assert availability.total_working_minutes == 24 * 60
        assert availability.contains(
            datetime(2026, 1, 5, 3, 0, tzinfo=UTC), datetime(2026, 1, 5, 3, 1, tzinfo=UTC)
        )

    def test_shift_calendar_respects_breaks(self) -> None:
        spec = CalendarSpec(
            id=2,
            code="TEST-2SHIFT",
            timezone="UTC",
            shifts=(
                ShiftSpec(day_of_week=1, start_minute=360, end_minute=720, label="A"),
                ShiftSpec(day_of_week=1, start_minute=840, end_minute=1200, label="B"),
            ),
            breaks=(BreakSpec(day_of_week=1, start_minute=720, end_minute=840, label="Lunch"),),
        )
        availability = _availability(spec, date(2026, 1, 5), date(2026, 1, 6))
        # Monday 05 Jan 2026: 06:00-12:00 and 14:00-20:00 -> 12 working hours
        assert availability.total_working_minutes == 12 * 60
        assert not availability.contains(
            datetime(2026, 1, 5, 12, 30, tzinfo=UTC), datetime(2026, 1, 5, 12, 31, tzinfo=UTC)
        )
        assert availability.contains(
            datetime(2026, 1, 5, 15, 0, tzinfo=UTC), datetime(2026, 1, 5, 15, 1, tzinfo=UTC)
        )

    def test_holiday_exception_removes_the_day(self) -> None:
        spec = CalendarSpec(
            id=3,
            code="TEST-HOL",
            timezone="UTC",
            shifts=(ShiftSpec(day_of_week=1, start_minute=360, end_minute=1080, label="A"),),
            exceptions=(ExceptionSpec(day=date(2026, 1, 5), kind="holiday", label="New year"),),
        )
        availability = _availability(spec, date(2026, 1, 5), date(2026, 1, 6))
        assert availability.total_working_minutes == 0

    def test_shutdown_exception_removes_a_window(self) -> None:
        spec = CalendarSpec(
            id=4,
            code="TEST-DOWN",
            timezone="UTC",
            shifts=(ShiftSpec(day_of_week=1, start_minute=360, end_minute=1080, label="A"),),
            exceptions=(
                ExceptionSpec(
                    day=date(2026, 1, 5),
                    kind="shutdown",
                    start_minute=480,
                    end_minute=660,
                    label="Maintenance",
                ),
            ),
        )
        availability = _availability(spec, date(2026, 1, 5), date(2026, 1, 6))
        assert availability.total_working_minutes == 12 * 60 - 3 * 60

    def test_overnight_shift_crosses_midnight(self) -> None:
        spec = CalendarSpec(
            id=5,
            code="TEST-NIGHT",
            timezone="UTC",
            shifts=(ShiftSpec(day_of_week=1, start_minute=1320, end_minute=1740, label="N"),),
        )
        availability = _availability(spec, date(2026, 1, 5), date(2026, 1, 7))
        # 22:00 -> 05:00 next day is 7 hours
        assert availability.total_working_minutes == 7 * 60
        assert availability.contains(
            datetime(2026, 1, 5, 23, 0, tzinfo=UTC), datetime(2026, 1, 5, 23, 1, tzinfo=UTC)
        )
        assert availability.contains(
            datetime(2026, 1, 6, 2, 0, tzinfo=UTC), datetime(2026, 1, 6, 2, 1, tzinfo=UTC)
        )

    def test_dst_day_still_exposes_full_continuous_time(self) -> None:
        spec = CalendarSpec(id=6, code="TEST-DST", timezone="Europe/Berlin", is_continuous=True)
        # 2026-03-29 is the European DST switch day.
        availability = _availability(spec, date(2026, 3, 29), date(2026, 3, 30))
        assert availability.total_working_minutes == 24 * 60

    def test_earliest_start_respects_working_segments(self) -> None:
        spec = CalendarSpec(
            id=7,
            code="TEST-EARLY",
            timezone="UTC",
            shifts=(ShiftSpec(day_of_week=1, start_minute=360, end_minute=720, label="A"),),
        )
        availability = _availability(spec, date(2026, 1, 5), date(2026, 1, 6))
        start = availability.earliest_start(60, not_before=datetime(2026, 1, 5, 8, tzinfo=UTC))
        assert start == datetime(2026, 1, 5, 8, tzinfo=UTC)

    def test_earliest_start_returns_none_when_impossible(self) -> None:
        spec = CalendarSpec(id=8, code="TEST-EMPTY", timezone="UTC")
        availability = _availability(spec, date(2026, 1, 5), date(2026, 1, 6))
        assert availability.earliest_start(60) is None

    def test_with_holes_removes_time(self) -> None:
        spec = CalendarSpec(id=9, code="TEST-HOLES", timezone="UTC", is_continuous=True)
        availability = _availability(spec, date(2026, 1, 5), date(2026, 1, 6))
        hole = Interval(datetime(2026, 1, 5, 6, tzinfo=UTC), datetime(2026, 1, 5, 12, tzinfo=UTC))
        updated = availability.with_holes([hole])
        assert updated.total_working_minutes == 24 * 60 - 6 * 60
        assert not updated.contains(
            datetime(2026, 1, 5, 8, tzinfo=UTC), datetime(2026, 1, 5, 8, 1, tzinfo=UTC)
        )

    def test_working_minutes_between(self) -> None:
        spec = CalendarSpec(
            id=10,
            code="TEST-WMB",
            timezone="UTC",
            shifts=(ShiftSpec(day_of_week=1, start_minute=360, end_minute=720, label="A"),),
        )
        availability = _availability(spec, date(2026, 1, 5), date(2026, 1, 6))
        minutes = availability.working_minutes_between(
            datetime(2026, 1, 5, 7, tzinfo=UTC), datetime(2026, 1, 5, 9, tzinfo=UTC)
        )
        assert minutes == 120


class TestBatchDuration:
    def test_batch_size_adds_internal_changeover_time(self) -> None:
        from types import SimpleNamespace

        operation = SimpleNamespace(
            duration_per_unit_minutes=2.0,
            fixed_minutes=10.0,
            batch_size=20,
            batch_setup_minutes=5.0,
            minimum_minutes=0.0,
        )
        # 41 units create 3 internal batches: two batch changeovers.
        assert _duration_for(operation, 41) == 2 * 41 + 10 + 2 * 5

    def test_missing_batch_size_keeps_a_single_lot(self) -> None:
        from types import SimpleNamespace

        operation = SimpleNamespace(
            duration_per_unit_minutes=2.0,
            fixed_minutes=10.0,
            batch_size=None,
            batch_setup_minutes=5.0,
            minimum_minutes=0.0,
        )
        assert _duration_for(operation, 41) == 2 * 41 + 10


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
