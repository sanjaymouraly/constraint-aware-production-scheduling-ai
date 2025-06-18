"""Calendar engine.

Turns calendar definitions (recurring shifts, recurring breaks, dated
exceptions and machine-specific overrides) into concrete **working segments**
inside a planning horizon.

Two calendar modes are supported by the scheduling engine:

``containment``
    An operation must be fully contained in a single working segment.  It is
    therefore never interrupted by a break, a shift change or a shutdown.
    This is the default and is the strictest — and therefore safest — reading
    of "operations are indivisible".

``stretch``
    An operation may span non-working periods; only its *working* minutes
    count against the required duration.  This requires every resource to use
    the same plant calendar (machine specific exceptions are rejected).

The engine always works with timezone-aware UTC instants.  Local plant time is
only used while expanding the weekly shift pattern into concrete days.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.timeutils import (
    UTC,
    Interval,
    add_minutes,
    ensure_utc,
    intersect_intervals,
    merge_intervals,
    subtract_intervals,
)

MIDNIGHT = time(0, 0)


@dataclass(frozen=True, slots=True)
class ShiftSpec:
    day_of_week: int  # ISO: 1 = Monday .. 7 = Sunday
    start_minute: int
    end_minute: int  # may exceed 1440 for overnight shifts
    label: str | None = None

    @property
    def is_overnight(self) -> bool:
        return self.end_minute > 1440

    @property
    def length_minutes(self) -> int:
        return self.end_minute - self.start_minute


@dataclass(frozen=True, slots=True)
class BreakSpec:
    day_of_week: int
    start_minute: int
    end_minute: int
    label: str | None = None


@dataclass(frozen=True, slots=True)
class ExceptionSpec:
    day: date
    kind: str  # holiday | shutdown | extra_shift | unavailable
    start_minute: int | None = None
    end_minute: int | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class CalendarSpec:
    """Immutable description of a working calendar."""

    id: int
    code: str
    timezone: str = "UTC"
    is_continuous: bool = False
    shifts: tuple[ShiftSpec, ...] = ()
    breaks: tuple[BreakSpec, ...] = ()
    exceptions: tuple[ExceptionSpec, ...] = ()

    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            return ZoneInfo("UTC")


def _local(moment: datetime, tz: ZoneInfo) -> datetime:
    return ensure_utc(moment).astimezone(tz)


def _localize(day: date, minute_of_day: int, tz: ZoneInfo) -> datetime:
    """Build a UTC instant for *minute_of_day* on local *day*.

    Handles overnight shifts (``minute_of_day`` may exceed 1440) and DST gaps
    (a non-existent local time is pushed forward to the next valid instant).
    """
    base_day = day + timedelta(days=minute_of_day // 1440)
    minute_in_day = minute_of_day % 1440
    naive = datetime.combine(base_day, MIDNIGHT) + timedelta(minutes=minute_in_day)
    localised = naive.replace(tzinfo=tz)
    # Detect a DST gap: the wall clock jumps forward, so the UTC offset differs.
    if localised.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != naive:
        localised = (naive + timedelta(hours=1)).replace(tzinfo=tz)
    return localised.astimezone(UTC)


@dataclass
class Availability:
    """Working-time availability of one resource over a horizon."""

    resource_id: int
    resource_kind: str
    horizon_start: datetime
    horizon_end: datetime
    working: list[Interval] = field(default_factory=list)
    continuous: bool = False

    # -- queries -----------------------------------------------------------
    @property
    def total_working_minutes(self) -> int:
        return sum(interval.minutes for interval in self.working)

    def contains(self, start: datetime, end: datetime) -> bool:
        """True when ``[start, end)`` lies fully inside a single working segment."""
        if self.continuous:
            return start >= self.horizon_start and end <= self.horizon_end
        probe = Interval(start, end)
        return any(segment.contains(probe) for segment in self.working)

    def overlaps_working(self, start: datetime, end: datetime) -> bool:
        if self.continuous:
            return start < self.horizon_end and self.horizon_start < end
        probe = Interval(start, end)
        return any(segment.overlaps(probe) for segment in self.working)

    def forbidden_intervals(self) -> list[Interval]:
        """Non-working intervals inside the horizon (the complement of `working`)."""
        if self.continuous:
            return []
        holes = subtract_intervals([Interval(self.horizon_start, self.horizon_end)], self.working)
        return holes

    def earliest_start(
        self,
        duration_minutes: int,
        not_before: datetime | None = None,
        not_after: datetime | None = None,
    ) -> datetime | None:
        """Earliest instant at which a block of *duration_minutes* fits.

        The block must be contained in one working segment (containment mode).
        """
        if duration_minutes <= 0:
            return None
        lower = max(self.horizon_start, not_before) if not_before else self.horizon_start
        upper = min(self.horizon_end, not_after) if not_after else self.horizon_end
        if upper - lower <= timedelta(0):
            return None
        for segment in self.working:
            if segment.end - segment.start < timedelta(minutes=duration_minutes):
                continue
            candidate = max(segment.start, lower)
            finish = candidate + timedelta(minutes=duration_minutes)
            if finish > segment.end:
                continue
            if finish > upper:
                continue
            return candidate
        return None

    def with_holes(self, holes: list[Interval]) -> Availability:
        """Return a copy with additional non-working intervals removed."""
        if not holes:
            return self
        clipped = [
            Interval(max(h.start, self.horizon_start), min(h.end, self.horizon_end))
            for h in holes
            if h.end > self.horizon_start and h.start < self.horizon_end
        ]
        working = subtract_intervals(self.working, clipped)
        return Availability(
            resource_id=self.resource_id,
            resource_kind=self.resource_kind,
            horizon_start=self.horizon_start,
            horizon_end=self.horizon_end,
            working=working,
            continuous=False,
        )

    def shifted(self, delay_minutes: int) -> Availability:
        """Return a copy whose availability starts *delay_minutes* later."""
        if delay_minutes <= 0:
            return self
        boundary = add_minutes(self.horizon_start, delay_minutes)
        working = [Interval(max(s.start, boundary), s.end) for s in self.working]
        return Availability(
            resource_id=self.resource_id,
            resource_kind=self.resource_kind,
            horizon_start=self.horizon_start,
            horizon_end=self.horizon_end,
            working=[w for w in working if w.minutes > 0],
            continuous=self.continuous,
        )

    def working_minutes_between(self, start: datetime, end: datetime) -> int:
        """Working minutes contained in ``[start, end)`` (stretch mode helper)."""
        if self.continuous:
            return int(
                (min(end, self.horizon_end) - max(start, self.horizon_start)).total_seconds() // 60
            )
        probe = Interval(start, end)
        total = 0
        for segment in self.working:
            lo = max(segment.start, probe.start)
            hi = min(segment.end, probe.end)
            if hi > lo:
                total += int((hi - lo).total_seconds() // 60)
        return total


class CalendarEngine:
    """Expands :class:`CalendarSpec` objects into concrete availability."""

    def __init__(self) -> None:
        self._cache: dict[tuple, Availability] = {}

    # -- public API --------------------------------------------------------
    def availability(
        self,
        spec: CalendarSpec,
        horizon_start: datetime,
        horizon_end: datetime,
        resource_id: int = 0,
        resource_kind: str = "calendar",
        extra_exceptions: list[ExceptionSpec] | None = None,
    ) -> Availability:
        horizon_start = ensure_utc(horizon_start)
        horizon_end = ensure_utc(horizon_end)
        # The cache key must contain *everything* that influences the result:
        # the shifts, the breaks, the dated exceptions and the extra
        # exceptions.  Two calendars that share an id but differ in their
        # exceptions must never share a cache entry.
        key = (
            spec.id,
            spec.code,
            spec.timezone,
            spec.is_continuous,
            tuple(sorted((s.day_of_week, s.start_minute, s.end_minute) for s in spec.shifts)),
            tuple(sorted((b.day_of_week, b.start_minute, b.end_minute) for b in spec.breaks)),
            tuple(sorted((e.day, e.kind, e.start_minute, e.end_minute) for e in spec.exceptions)),
            horizon_start,
            horizon_end,
            resource_id,
            resource_kind,
            tuple(
                sorted(
                    (e.day, e.kind, e.start_minute, e.end_minute) for e in (extra_exceptions or [])
                )
            ),
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        if spec.is_continuous:
            availability = Availability(
                resource_id=resource_id,
                resource_kind=resource_kind,
                horizon_start=horizon_start,
                horizon_end=horizon_end,
                working=[Interval(horizon_start, horizon_end)],
                continuous=True,
            )
            self._cache[key] = availability
            return availability

        working = self._expand(spec, horizon_start, horizon_end, extra_exceptions or [])
        working = merge_intervals(working)
        availability = Availability(
            resource_id=resource_id,
            resource_kind=resource_kind,
            horizon_start=horizon_start,
            horizon_end=horizon_end,
            working=working,
        )
        self._cache[key] = availability
        return availability

    def clear_cache(self) -> None:
        self._cache.clear()

    # -- internals ---------------------------------------------------------
    def _expand(
        self,
        spec: CalendarSpec,
        horizon_start: datetime,
        horizon_end: datetime,
        extra_exceptions: list[ExceptionSpec],
    ) -> list[Interval]:
        tz = spec.zone()
        # Local dates covered by the horizon (one extra day either side so that
        # overnight shifts spilling across midnight are fully covered).
        local_start = (horizon_start.astimezone(tz) - timedelta(days=1)).date()
        local_end = (horizon_end.astimezone(tz) + timedelta(days=1)).date()

        all_exceptions = list(spec.exceptions) + list(extra_exceptions)
        exceptions_by_day: dict[date, list[ExceptionSpec]] = {}
        for exc in all_exceptions:
            exceptions_by_day.setdefault(exc.day, []).append(exc)

        raw: list[Interval] = []
        day = local_start
        while day <= local_end:
            iso_weekday = day.isoweekday()
            day_intervals: list[Interval] = []
            for shift in spec.shifts:
                if shift.day_of_week != iso_weekday:
                    continue
                start = _localize(day, shift.start_minute, tz)
                end = _localize(day, shift.end_minute, tz)
                if end > start:
                    day_intervals.append(Interval(start, end))
            day_intervals = merge_intervals(day_intervals)

            # Recurring breaks carve holes out of the day.
            holes: list[Interval] = []
            for brk in spec.breaks:
                if brk.day_of_week != iso_weekday:
                    continue
                b_start = _localize(day, brk.start_minute, tz)
                b_end = _localize(day, brk.end_minute, tz)
                if b_end > b_start:
                    holes.append(Interval(b_start, b_end))

            # Dated exceptions.
            day_exceptions = exceptions_by_day.get(day, [])
            for exc in day_exceptions:
                if exc.kind in {"holiday", "shutdown", "unavailable"}:
                    if exc.start_minute is not None and exc.end_minute is not None:
                        # A windowed exception (e.g. a maintenance shutdown) only
                        # removes that window, not the whole day.
                        s = exc.start_minute
                        e = exc.end_minute
                        if e > s:
                            holes.append(Interval(_localize(day, s, tz), _localize(day, e, tz)))
                    else:
                        day_start = _localize(day, 0, tz)
                        day_end = _localize(day, 1440, tz)
                        holes.append(Interval(day_start, day_end))
                elif exc.kind == "extra_shift":
                    s = exc.start_minute or 0
                    e = exc.end_minute if exc.end_minute is not None else 1440
                    day_intervals.append(Interval(_localize(day, s, tz), _localize(day, e, tz)))

            remaining = subtract_intervals(day_intervals, holes)
            raw.extend(remaining)
            day += timedelta(days=1)

        # Clip to the horizon.
        horizon = Interval(horizon_start, horizon_end)
        return intersect_intervals(merge_intervals(raw), [horizon])


@lru_cache(maxsize=1)
def get_engine() -> CalendarEngine:
    return CalendarEngine()
