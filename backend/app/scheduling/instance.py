"""Immutable scheduling instance.

A *SchedulingInstance* is the single source of truth handed to both the FCFS
baseline and the CP-SAT optimiser.  It is produced from the database by
:func:`build_instance` and never mutated afterwards, which guarantees that the
baseline and the optimiser see **exactly the same constraints and data**.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from app.domain.calendars import Availability
from app.domain.timeutils import Interval, add_minutes, ensure_utc

CalendarMode = Literal["containment", "stretch"]


@dataclass(frozen=True, slots=True)
class Task:
    """A single indivisible operation to be scheduled."""

    key: str
    work_order_id: int
    work_order_code: str
    customer_code: str
    operation_id: int
    sequence: int
    name: str
    machine_type: str
    eligible_machine_ids: tuple[int, ...]
    duration_minutes: int
    required_skills: tuple[str, ...]
    crew_size: int
    transfer_time_minutes: int
    setup_family: str
    release_at: datetime
    due_at: datetime
    hard_deadline: datetime | None
    priority: int
    penalty_per_hour: float
    hourly_cost: float
    predecessor_keys: tuple[str, ...]
    #: (predecessor key, transfer minutes) pairs.
    transfer_after_predecessor: tuple[tuple[str, int], ...] = ()
    material_requirements: tuple[tuple[int, float], ...] = ()
    #: Operations frozen by a published/committed plan.
    locked: bool = False
    locked_start: datetime | None = None
    locked_end: datetime | None = None
    locked_machine_id: int | None = None
    locked_operator_id: int | None = None
    #: Operations already physically completed (never rescheduled).
    completed: bool = False
    allow_split: bool = False

    @property
    def latest_end(self) -> datetime:
        return self.hard_deadline or self.due_at


@dataclass(frozen=True, slots=True)
class MachineResource:
    id: int
    code: str
    name: str
    machine_type: str
    supported_families: frozenset[str]
    capabilities: frozenset[str]
    hourly_cost: float
    initial_setup_minutes: int
    setup_requires_operator: bool
    availability: Availability
    #: setup[(from_family, to_family)] -> minutes (0 when not configured).
    setup_minutes: dict[tuple[str, str], int] = field(default_factory=dict)

    def setup_for(self, from_family: str, to_family: str) -> int:
        return self.setup_minutes.get((from_family, to_family), 0)

    def max_setup_into(self, family: str) -> int:
        if not self.setup_minutes:
            return 0
        return max(
            (value for (frm, to), value in self.setup_minutes.items() if to == family),
            default=0,
        )


@dataclass(frozen=True, slots=True)
class OperatorResource:
    id: int
    code: str
    name: str
    skills: frozenset[str]
    certifications: frozenset[str]
    eligible_machine_types: frozenset[str]
    eligible_machine_ids: frozenset[int]
    hourly_cost: float
    weekly_overtime_limit_minutes: int
    weekly_regular_minutes: int
    availability: Availability

    def qualified_for(self, task: Task) -> bool:
        if not set(task.required_skills).issubset(self.skills):
            return False
        return True

    def eligible_for_machine(self, machine: MachineResource) -> bool:
        if self.eligible_machine_ids:
            return machine.id in self.eligible_machine_ids
        if self.eligible_machine_types:
            return machine.machine_type in self.eligible_machine_types
        return True


@dataclass(frozen=True, slots=True)
class MaterialStock:
    id: int
    sku: str
    name: str
    on_hand: float
    #: (arrival instant, quantity) pairs sorted by time.
    receipts: tuple[tuple[datetime, float], ...] = ()

    def available_at(self, moment: datetime) -> float:
        total = self.on_hand
        for arrival, qty in self.receipts:
            if arrival <= moment:
                total += qty
            else:
                break
        return total

    def total_available(self) -> float:
        return self.on_hand + sum(q for _, q in self.receipts)

    def receipt_boundaries(self) -> list[tuple[datetime, float]]:
        """Check points for the cumulative inventory constraint.

        Returns ``(instant, available_quantity)`` pairs where the available
        quantity is the stock that is on hand *strictly before* the instant.
        """
        boundaries: list[tuple[datetime, float]] = []
        running = self.on_hand
        for arrival, qty in self.receipts:
            boundaries.append((arrival, running))
            running += qty
        return boundaries


@dataclass
class SchedulingInstance:
    """Everything a solver needs."""

    tasks: list[Task]
    machines: dict[int, MachineResource]
    operators: dict[int, OperatorResource]
    materials: dict[int, MaterialStock]
    horizon_start: datetime
    horizon_end: datetime
    calendar_mode: CalendarMode = "containment"
    granularity_minutes: int = 1
    #: Objective weights (all normalised before use in a linear objective).
    weights: dict[str, float] = field(default_factory=dict)
    objective_name: str = "weighted_tardiness"
    #: Machine/operator ids that are unavailable (used by scenarios).
    machine_exclusions: set[int] = field(default_factory=set)
    operator_exclusions: set[int] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    # -- lookups ----------------------------------------------------------
    def task_by_key(self, key: str) -> Task:
        return self._index()[key]

    def _index(self) -> dict[str, Task]:
        cached = getattr(self, "_task_index", None)
        if cached is None:
            cached = {t.key: t for t in self.tasks}
            object.__setattr__(self, "_task_index", cached)
        return cached

    def eligible_machines(self, task: Task) -> list[MachineResource]:
        out = []
        for mid in task.eligible_machine_ids:
            machine = self.machines.get(mid)
            if machine is None or mid in self.machine_exclusions:
                continue
            if machine.machine_type != task.machine_type:
                continue
            if machine.supported_families and task.setup_family not in machine.supported_families:
                continue
            out.append(machine)
        return out

    def eligible_operators(self, task: Task, machine: MachineResource) -> list[OperatorResource]:
        out = []
        for operator in self.operators.values():
            if operator.id in self.operator_exclusions:
                continue
            if not operator.qualified_for(task):
                continue
            if not operator.eligible_for_machine(machine):
                continue
            out.append(operator)
        return out

    def total_required_minutes(self) -> int:
        return sum(t.duration_minutes for t in self.tasks)

    def machine_available_minutes(self, machine_id: int) -> int:
        machine = self.machines[machine_id]
        return machine.availability.total_working_minutes


def earliest_finish(
    availability: Availability, earliest_start: datetime, duration_minutes: int
) -> datetime | None:
    """Earliest finish of a block of *duration_minutes* inside *availability*."""
    start = availability.earliest_start(duration_minutes, not_before=earliest_start)
    if start is None:
        return None
    return add_minutes(start, duration_minutes)


def horizon_bounds(
    tasks: Sequence[Task], default_days: int, now: datetime
) -> tuple[datetime, datetime]:
    """Derive a sensible planning horizon from the workload."""
    if not tasks:
        return now, now + timedelta(days=default_days)
    release = min(t.release_at for t in tasks)
    due = max(t.due_at for t in tasks)
    start = min(release, now) - timedelta(days=1)
    end = max(due, now + timedelta(days=default_days)) + timedelta(days=2)
    return ensure_utc(start), ensure_utc(end)


def unique_sorted(values: Iterable[int]) -> tuple[int, ...]:
    return tuple(sorted(set(values)))


def intervals_overlap(a: Interval, b: Interval) -> bool:
    return a.overlaps(b)
