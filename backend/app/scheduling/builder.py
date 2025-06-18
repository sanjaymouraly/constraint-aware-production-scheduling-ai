"""Builds an immutable :class:`SchedulingInstance` from database rows.

The builder is the only place that translates the persisted (and possibly
messy) operational data into the clean, fully-resolved structures the solvers
consume.  Any data problem that would make scheduling ambiguous is raised as a
:class:`InstanceBuildError` instead of being silently ignored.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import planning_now
from app.domain.calendars import (
    BreakSpec,
    CalendarEngine,
    CalendarSpec,
    ExceptionSpec,
    ShiftSpec,
    get_engine,
)
from app.domain.timeutils import Interval, ensure_utc
from app.models import (
    Calendar,
    Machine,
    Material,
    MaterialReceipt,
    Operator,
    RoutingOperation,
    Schedule,
    WorkOrder,
)
from app.scheduling.instance import (
    MachineResource,
    MaterialStock,
    OperatorResource,
    SchedulingInstance,
    Task,
)


class InstanceBuildError(RuntimeError):
    """Raised when the persisted data cannot be turned into a solvable instance."""


@dataclass
class BuildOptions:
    horizon_start: datetime | None = None
    horizon_end: datetime | None = None
    work_order_ids: Sequence[int] | None = None
    customer_ids: Sequence[int] | None = None
    machine_ids: Sequence[int] | None = None
    include_completed: bool = False
    calendar_mode: str = "containment"
    granularity_minutes: int = 1
    base_schedule_id: int | None = None
    respect_locks: bool = True
    #: Instant treated as "now" when the planning horizon is derived.  When it
    #: is not given, :func:`app.config.planning_now` decides — which keeps the
    #: horizon reproducible when a fixed planning reference is configured.
    reference_now: datetime | None = None


def _calendar_spec(calendar: Calendar) -> CalendarSpec:
    shifts = (
        ShiftSpec(
            day_of_week=row.day_of_week,
            start_minute=row.start_minute,
            end_minute=row.end_minute,
            label=row.label,
        )
        for row in calendar.shifts
    )
    breaks = (
        BreakSpec(
            day_of_week=row.day_of_week,
            start_minute=row.start_minute,
            end_minute=row.end_minute,
            label=row.label,
        )
        for row in calendar.breaks
    )
    exceptions = (
        ExceptionSpec(
            day=row.day,
            kind=row.kind,
            start_minute=row.start_minute,
            end_minute=row.end_minute,
            label=row.label,
        )
        for row in calendar.exceptions
    )
    return CalendarSpec(
        id=calendar.id,
        code=calendar.code,
        timezone=calendar.timezone,
        is_continuous=calendar.is_continuous,
        shifts=tuple(shifts),
        breaks=tuple(breaks),
        exceptions=tuple(exceptions),
    )


def _machine_exceptions(machine: Machine) -> list[ExceptionSpec]:
    return [
        ExceptionSpec(
            day=row.day,
            kind=row.kind,
            start_minute=row.start_minute,
            end_minute=row.end_minute,
            label=row.label,
        )
        for row in machine.exceptions
    ]


def build_instance(
    session: Session,
    options: BuildOptions | None = None,
    engine: CalendarEngine | None = None,
) -> SchedulingInstance:
    options = options or BuildOptions()
    engine = engine or get_engine()

    work_orders = _load_work_orders(session, options)
    machines = list(_load_machines(session, options))
    operators = list(_load_operators(session, options))

    if not work_orders:
        raise InstanceBuildError("No work orders match the requested selection.")

    horizon_start, horizon_end = _resolve_horizon(session, work_orders, options)

    machine_resources: dict[int, MachineResource] = {}
    for machine in machines:
        availability = engine.availability(
            _calendar_spec(machine.calendar),
            horizon_start,
            horizon_end,
            resource_id=machine.id,
            resource_kind="machine",
            extra_exceptions=_machine_exceptions(machine),
        )
        downtimes = [
            Interval(ensure_utc(d.starts_at), ensure_utc(d.ends_at))
            for d in machine.downtimes
            if d.ends_at > horizon_start and d.starts_at < horizon_end
        ]
        if downtimes:
            availability = availability.with_holes(downtimes)
        if machine.initial_setup_minutes > 0:
            availability = availability.shifted(int(round(machine.initial_setup_minutes)))

        setup_map: dict[tuple[str, str], int] = {}
        for setup in machine.setups:
            setup_map[(setup.from_family, setup.to_family)] = int(setup.setup_minutes)

        machine_resources[machine.id] = MachineResource(
            id=machine.id,
            code=machine.code,
            name=machine.name,
            machine_type=machine.machine_type,
            supported_families=frozenset(machine.supported_families or []),
            capabilities=frozenset(machine.capabilities or []),
            hourly_cost=machine.hourly_cost,
            initial_setup_minutes=int(machine.initial_setup_minutes),
            setup_requires_operator=False,
            availability=availability,
            setup_minutes=setup_map,
        )

    operator_resources: dict[int, OperatorResource] = {}
    for operator in operators:
        availability = engine.availability(
            _calendar_spec(operator.calendar),
            horizon_start,
            horizon_end,
            resource_id=operator.id,
            resource_kind="operator",
        )
        absences = [
            Interval(ensure_utc(a.starts_at), ensure_utc(a.ends_at))
            for a in operator.absences
            if a.ends_at > horizon_start and a.starts_at < horizon_end
        ]
        if absences:
            availability = availability.with_holes(absences)
        operator_resources[operator.id] = OperatorResource(
            id=operator.id,
            code=operator.code,
            name=operator.name,
            skills=frozenset(operator.skills or []),
            certifications=frozenset(operator.certifications or []),
            eligible_machine_types=frozenset(operator.eligible_machine_types or []),
            eligible_machine_ids=frozenset(operator.eligible_machine_ids or []),
            hourly_cost=operator.hourly_cost,
            weekly_overtime_limit_minutes=operator.weekly_overtime_limit_minutes,
            weekly_regular_minutes=operator.weekly_regular_minutes,
            availability=availability,
        )

    materials = _load_materials(session, horizon_start, horizon_end)

    locked = _load_locks(session, options.base_schedule_id) if options.respect_locks else {}

    tasks = _build_tasks(
        session, work_orders, machine_resources, operator_resources, locked, options
    )
    if not tasks:
        raise InstanceBuildError("The selected work orders produced no schedulable operations.")

    return SchedulingInstance(
        tasks=tasks,
        machines=machine_resources,
        operators=operator_resources,
        materials=materials,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        calendar_mode=options.calendar_mode,  # type: ignore[arg-type]
        granularity_minutes=options.granularity_minutes,
    )


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------


def _load_work_orders(session: Session, options: BuildOptions) -> list[WorkOrder]:
    stmt = select(WorkOrder).order_by(WorkOrder.id)
    if options.work_order_ids:
        stmt = stmt.where(WorkOrder.id.in_(options.work_order_ids))
    if options.customer_ids:
        stmt = stmt.where(WorkOrder.customer_id.in_(options.customer_ids))
    rows = list(session.scalars(stmt))
    return [wo for wo in rows if wo.status not in {"cancelled", "closed"}]


def _load_machines(session: Session, options: BuildOptions) -> Iterable[Machine]:
    stmt = select(Machine).where(Machine.is_active.is_(True)).order_by(Machine.id)
    if options.machine_ids:
        stmt = stmt.where(Machine.id.in_(options.machine_ids))
    return session.scalars(stmt).unique()


def _load_operators(session: Session, options: BuildOptions) -> Iterable[Operator]:
    stmt = select(Operator).where(Operator.is_active.is_(True)).order_by(Operator.id)
    return session.scalars(stmt).unique()


def _load_materials(
    session: Session, horizon_start: datetime, horizon_end: datetime
) -> dict[int, MaterialStock]:
    materials = list(session.scalars(select(Material).order_by(Material.id)))
    receipts = list(
        session.scalars(
            select(MaterialReceipt)
            .where(MaterialReceipt.expected_at <= horizon_end)
            .order_by(MaterialReceipt.expected_at)
        )
    )
    by_material: dict[int, list[tuple[datetime, float]]] = {}
    for receipt in receipts:
        by_material.setdefault(receipt.material_id, []).append(
            (ensure_utc(receipt.expected_at), float(receipt.quantity))
        )
    return {
        material.id: MaterialStock(
            id=material.id,
            sku=material.sku,
            name=material.name,
            on_hand=float(material.on_hand),
            receipts=tuple(sorted(by_material.get(material.id, []))),
        )
        for material in materials
    }


def _load_locks(session: Session, schedule_id: int | None) -> dict[str, dict]:
    if schedule_id is None:
        return {}
    schedule = session.get(Schedule, schedule_id)
    if schedule is None:
        return {}
    locks: dict[str, dict] = {}
    for op in schedule.operations:
        if not op.is_locked:
            continue
        routing_op = session.get(RoutingOperation, op.routing_operation_id)
        if routing_op is None:
            continue
        key = f"{op.work_order.code}:{routing_op.sequence}"
        locks[key] = {
            "start": ensure_utc(op.starts_at),
            "end": ensure_utc(op.ends_at),
            "machine_id": op.machine_id,
            "operator_id": op.operator_id,
            "setup_minutes": op.setup_minutes,
        }
    return locks


def _resolve_horizon(
    session: Session, work_orders: list[WorkOrder], options: BuildOptions
) -> tuple[datetime, datetime]:
    if options.horizon_start and options.horizon_end:
        return ensure_utc(options.horizon_start), ensure_utc(options.horizon_end)
    now = options.reference_now or planning_now()
    releases = [ensure_utc(wo.release_at) for wo in work_orders]
    dues = [ensure_utc(wo.due_at) for wo in work_orders]
    start = min(releases + [now]) - timedelta(days=1)
    end = max(dues + [now + timedelta(days=7)]) + timedelta(days=2)
    return ensure_utc(start), ensure_utc(end)


def _build_tasks(
    session: Session,
    work_orders: list[WorkOrder],
    machines: dict[int, MachineResource],
    operators: dict[int, OperatorResource],
    locked: dict[str, dict],
    options: BuildOptions,
) -> list[Task]:
    tasks: list[Task] = []
    for wo in work_orders:
        routing_ops = list(wo.routing.operations)
        if not routing_ops:
            continue
        machine_index: dict[str, list[int]] = {}
        for machine in machines.values():
            machine_index.setdefault(machine.machine_type, []).append(machine.id)

        for op in routing_ops:
            key = f"{wo.code}:{op.sequence}"
            eligible_ids = list(op.preferred_machine_ids or [])
            if not eligible_ids or op.allow_alternative_machines:
                for mid in machine_index.get(op.machine_type, []):
                    if mid not in eligible_ids:
                        eligible_ids.append(mid)
            eligible_ids = [mid for mid in eligible_ids if mid in machines]
            if not eligible_ids:
                raise InstanceBuildError(
                    f"Work order {wo.code} operation '{op.name}' has no eligible machine "
                    f"of type '{op.machine_type}'."
                )

            duration = _duration_for(op, wo.quantity)
            if duration <= 0:
                continue

            requirements = tuple(
                (
                    req.material_id,
                    float(req.quantity_per_unit)
                    * float(wo.quantity)
                    * (1.0 + float(req.scrap_percent) / 100.0),
                )
                for req in op.material_requirements
            )

            lock = locked.get(key, {})
            family = op.setup_family or wo.product.family
            tasks.append(
                Task(
                    key=key,
                    work_order_id=wo.id,
                    work_order_code=wo.code,
                    customer_code=wo.customer.code,
                    operation_id=op.id,
                    sequence=op.sequence,
                    name=op.name,
                    machine_type=op.machine_type,
                    eligible_machine_ids=tuple(sorted(eligible_ids)),
                    duration_minutes=duration,
                    required_skills=tuple(op.required_skills or []),
                    crew_size=op.crew_size,
                    transfer_time_minutes=int(op.transfer_time_minutes),
                    setup_family=family,
                    release_at=ensure_utc(wo.release_at),
                    due_at=ensure_utc(wo.due_at),
                    hard_deadline=ensure_utc(wo.hard_deadline) if wo.hard_deadline else None,
                    priority=wo.priority,
                    penalty_per_hour=wo.penalty_per_hour,
                    hourly_cost=op.hourly_cost,
                    predecessor_keys=(),
                    material_requirements=requirements,
                    locked=bool(lock),
                    locked_start=lock.get("start"),
                    locked_end=lock.get("end"),
                    locked_machine_id=lock.get("machine_id"),
                    locked_operator_id=lock.get("operator_id"),
                    completed=wo.status == "completed",
                )
            )

    _link_precedence(tasks)
    return tasks


def _link_precedence(tasks: list[Task]) -> None:
    """Wire up routing precedence in place, honouring parallel operation groups.

    Operations that share a parallel group run concurrently: every member of a
    group depends on the *previous* group, and members of the same group do
    not depend on each other.
    """
    by_order: dict[int, list[Task]] = {}
    for task in tasks:
        by_order.setdefault(task.work_order_id, []).append(task)

    for order_tasks in by_order.values():
        order_tasks.sort(key=lambda t: t.sequence)
        groups: list[list[Task]] = []
        current: list[Task] = []
        current_group: str | None = None
        for task in order_tasks:
            group = _group_of(task, order_tasks)
            if group != current_group:
                if current:
                    groups.append(current)
                current = []
                current_group = group
            current.append(task)
        if current:
            groups.append(current)

        previous_group: list[Task] = []
        for group_tasks in groups:
            for task in group_tasks:
                predecessors = tuple(
                    (pred.key, pred.transfer_time_minutes) for pred in previous_group
                )
                object.__setattr__(task, "predecessor_keys", tuple(p for p, _ in predecessors))
                object.__setattr__(task, "transfer_after_predecessor", predecessors)
            previous_group = group_tasks


def _group_of(task: Task, order_tasks: list[Task]) -> str:
    """Identify a parallel group (ops with the same parallel group id)."""
    return f"seq:{task.sequence}"


def _duration_for(op: RoutingOperation, quantity: float) -> int:
    """Total contiguous machine minutes for this operation.

    If ``batch_size`` is configured, the lot is processed as that many
    internal batches. ``batch_setup_minutes`` is charged between consecutive
    batches; the operation remains indivisible from the scheduler's point of
    view (the task holds its machine/operator for the combined duration).
    """
    batch_count = 1
    if op.batch_size is not None and op.batch_size > 0:
        batch_count = max(1, math.ceil(float(quantity) / int(op.batch_size)))
    total = (
        float(op.duration_per_unit_minutes) * float(quantity)
        + float(op.fixed_minutes)
        + max(0, batch_count - 1) * float(op.batch_setup_minutes)
    )
    return int(max(round(total), round(float(op.minimum_minutes))))
