"""Time utilities.

The whole scheduling engine works on **integer minutes** measured from a
horizon origin.  Wall-clock timestamps are always timezone-aware UTC; local
plant time only appears when a calendar is expanded into concrete segments.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

MINUTES_PER_DAY = 1440
MINUTES_PER_HOUR = 60
UTC = UTC


def ensure_utc(value: datetime) -> datetime:
    """Return *value* as a timezone-aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def minutes_between(start: datetime, end: datetime) -> int:
    """Whole minutes between two instants (end - start), rounded to nearest."""
    delta = ensure_utc(end) - ensure_utc(start)
    return int(round(delta.total_seconds() / 60.0))


def add_minutes(moment: datetime, minutes: int | float) -> datetime:
    return ensure_utc(moment) + timedelta(minutes=minutes)


def ceil_to_minute(moment: datetime) -> datetime:
    m = ensure_utc(moment)
    if m.second or m.microsecond:
        return (m + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return m


def floor_to_minute(moment: datetime) -> datetime:
    return ensure_utc(moment).replace(second=0, microsecond=0)


def start_of_day(moment: datetime) -> datetime:
    return floor_to_minute(moment).replace(hour=0, minute=0)


def iter_days(start: datetime, end: datetime):
    """Yield each calendar date (UTC) between *start* and *end* inclusive."""
    current = start.date()
    last = end.date()
    while current <= last:
        yield current
        current += timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Interval:
    """A half-open time interval ``[start, end)`` in UTC."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", ensure_utc(self.start))
        object.__setattr__(self, "end", ensure_utc(self.end))

    @property
    def minutes(self) -> int:
        return max(0, minutes_between(self.start, self.end))

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def overlaps(self, other: Interval) -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, other: Interval) -> bool:
        return self.start <= other.start and other.end <= self.end

    def shift(self, minutes: int) -> Interval:
        return Interval(add_minutes(self.start, minutes), add_minutes(self.end, minutes))

    def __lt__(self, other: Interval) -> bool:
        return (self.start, self.end) < (other.start, other.end)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Interval({self.start.isoformat()} -> {self.end.isoformat()})"


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Merge overlapping/adjacent intervals into a sorted, disjoint list."""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda i: (i.start, i.end))
    merged = [ordered[0]]
    for interval in ordered[1:]:
        last = merged[-1]
        if interval.start <= last.end:
            if interval.end > last.end:
                merged[-1] = Interval(last.start, interval.end)
        else:
            merged.append(interval)
    return merged


def subtract_intervals(base: list[Interval], holes: list[Interval]) -> list[Interval]:
    """Remove every *hole* from the (already merged) *base* intervals."""
    if not holes:
        return list(base)
    result: list[Interval] = []
    holes = merge_intervals(holes)
    for interval in base:
        cursor = interval.start
        for hole in holes:
            if hole.end <= cursor:
                continue
            if hole.start >= interval.end:
                break
            if hole.start > cursor:
                result.append(Interval(cursor, min(hole.start, interval.end)))
            cursor = max(cursor, hole.end)
            if cursor >= interval.end:
                break
        if cursor < interval.end:
            result.append(Interval(cursor, interval.end))
    return [i for i in result if i.minutes > 0]


def intersect_intervals(left: list[Interval], right: list[Interval]) -> list[Interval]:
    """Intersection of two merged interval lists."""
    result: list[Interval] = []
    i = j = 0
    while i < len(left) and j < len(right):
        start = max(left[i].start, right[j].start)
        end = min(left[i].end, right[j].end)
        if start < end:
            result.append(Interval(start, end))
        if left[i].end < right[j].end:
            i += 1
        else:
            j += 1
    return result


def local_date_of(moment: datetime, tz_name: str) -> date:
    """Local calendar date of *moment* in the given IANA timezone."""
    return ensure_utc(moment).astimezone(_zone(tz_name)).date()


def _zone(tz_name: str):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return UTC
