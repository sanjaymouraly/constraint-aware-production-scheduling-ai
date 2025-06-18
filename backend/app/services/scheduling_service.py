"""Application service layer for scheduling.

This module owns the lifecycle of a plan: building the instance, running the
requested engine, validating the outcome, persisting it as an immutable
version and comparing two versions.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import default_worker_count, settings
from app.domain.timeutils import ensure_utc
from app.models import (
    OptimizationRun,
    Schedule,
    ScheduleOperation,
    WorkOrder,
)
from app.scheduling.builder import BuildOptions, InstanceBuildError, build_instance
from app.scheduling.cpsat import CpSatOptions, CpSatScheduler
from app.scheduling.fcfs import run_fcfs
from app.scheduling.instance import SchedulingInstance
from app.scheduling.metrics import compare_metrics, compute_metrics
from app.scheduling.postprocess import apply_setup_gaps
from app.scheduling.result import Assignment, ScheduleResult
from app.scheduling.validator import ValidationReport, validate_schedule
from app.services import audit

EngineName = Literal["fcfs", "cpsat"]


class SchedulingError(RuntimeError):
    """Raised when a scheduling request cannot be executed at all."""


class TooManyOptimizations(SchedulingError):
    """Raised when the concurrent-optimisation limit has been reached."""


class ConcurrencyGuard:
    """Process wide guard that limits the number of parallel optimisations.

    The solver is CPU bound, so running more optimisations than the machine
    can serve would slow every request down without improving any result.
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self._lock = threading.Lock()
        self._active: dict[str, str] = {}

    @property
    def active(self) -> int:
        with self._lock:
            return len(self._active)

    def acquire(self, label: str = "", wait_seconds: float = 0.0) -> str | None:
        """Reserve a slot.

        Returns a token, or ``None`` when the limit is reached.  When
        ``wait_seconds`` is greater than zero the call waits that long for a
        free slot before giving up (used by background jobs, which must not be
        rejected).
        """
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            with self._lock:
                if len(self._active) < self.limit:
                    token = uuid.uuid4().hex
                    self._active[token] = label
                    return token
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)

    def release(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._active.pop(token, None)


#: Process wide guard for concurrent optimisations.
optimization_guard = ConcurrencyGuard(settings.max_concurrent_optimizations)


@dataclass
class RunRequest:
    engine: EngineName = "cpsat"
    horizon_start: datetime | None = None
    horizon_end: datetime | None = None
    work_order_ids: Sequence[int] | None = None
    customer_ids: Sequence[int] | None = None
    machine_ids: Sequence[int] | None = None
    time_limit_seconds: float = 20.0
    workers: int = field(default_factory=default_worker_count)
    random_seed: int = 42
    objective_name: str = "weighted_tardiness"
    weights: dict[str, float] = field(default_factory=dict)
    objective_mode: str = "weighted"
    log_search: bool = False
    name: str | None = None
    notes: str | None = None
    scenario_id: int | None = None
    parent_schedule_id: int | None = None
    publish: bool = False
    #: Schedule whose locked operations must be preserved.
    base_schedule_id: int | None = None
    #: Schedule used as the stability reference.
    stability_schedule_id: int | None = None
    actor: str = "system"
    use_baseline_hint: bool = True
    #: Instant treated as "now" when the planning horizon is derived.  ``None``
    #: uses the configured planning reference (or the wall clock).
    reference_now: datetime | None = None


@dataclass
class RunOutcome:
    result: ScheduleResult
    instance: SchedulingInstance
    validation: ValidationReport
    metrics: dict[str, Any]
    schedule: Schedule | None = None
    run: OptimizationRun | None = None
    baseline_metrics: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None


def build_options(request: RunRequest) -> BuildOptions:
    return BuildOptions(
        horizon_start=request.horizon_start,
        horizon_end=request.horizon_end,
        work_order_ids=list(request.work_order_ids) if request.work_order_ids else None,
        customer_ids=list(request.customer_ids) if request.customer_ids else None,
        machine_ids=list(request.machine_ids) if request.machine_ids else None,
        base_schedule_id=request.base_schedule_id,
        reference_now=request.reference_now,
    )


def run_schedule(
    session: Session,
    request: RunRequest,
    persist: bool = True,
    cancel_check: Callable[[], bool] | None = None,
    use_guard: bool = True,
    guard_wait_seconds: float = 0.0,
) -> RunOutcome:
    """Execute one scheduling run and (optionally) persist the outcome.

    ``cancel_check`` is an optional cooperative cancellation hook.  It is
    polled by the CP-SAT solver; when it returns ``True`` the search stops and
    the best feasible solution found so far is reported with status
    ``CANCELLED``.

    ``use_guard`` enables the process wide concurrency limit; background jobs
    and the CLI disable it so that a queued job is never rejected.
    """
    token = optimization_guard.acquire(
        f"{request.engine}:{request.name or 'run'}", wait_seconds=guard_wait_seconds
    )
    if token is None and use_guard:
        raise TooManyOptimizations(
            f"Too many concurrent optimisations: the limit of "
            f"{optimization_guard.limit} is already reached. Please try again shortly."
        )
    try:
        return _run_schedule(session, request, persist=persist, cancel_check=cancel_check)
    finally:
        optimization_guard.release(token)


def _run_schedule(
    session: Session,
    request: RunRequest,
    persist: bool = True,
    cancel_check: Callable[[], bool] | None = None,
) -> RunOutcome:
    """The actual execution of a scheduling run (see :func:`run_schedule`)."""
    try:
        instance = build_instance(session, build_options(request))
    except InstanceBuildError as exc:
        raise SchedulingError(str(exc)) from exc

    baseline_result: ScheduleResult | None = None
    baseline_metrics: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None

    if request.engine == "fcfs":
        result = run_fcfs(instance)
    else:
        options = CpSatOptions(
            time_limit_seconds=request.time_limit_seconds,
            workers=request.workers,
            random_seed=request.random_seed,
            objective_name=request.objective_name,
            weights=dict(request.weights),
            objective_mode=request.objective_mode,
            log_search=request.log_search,
            cancel_check=cancel_check,
        )
        scheduler = CpSatScheduler(instance, options)
        scheduler.build()
        if request.use_baseline_hint:
            baseline_result = run_fcfs(instance)
            if baseline_result.status == "FEASIBLE":
                scheduler.add_hint(baseline_result.assignments)
        if request.stability_schedule_id is not None:
            reference = load_reference(session, request.stability_schedule_id)
            options.stability_reference = reference
            scheduler._build_stability(reference)
        result = scheduler.solve()

    apply_setup_gaps(instance, result.assignments)
    validation = validate_schedule(instance, result.assignments)
    metrics = compute_metrics(instance, result.assignments, solver_runtime_ms=result.runtime_ms)
    result.metrics = metrics

    if baseline_result is not None:
        apply_setup_gaps(instance, baseline_result.assignments)
        baseline_metrics = compute_metrics(
            instance, baseline_result.assignments, solver_runtime_ms=baseline_result.runtime_ms
        )
        comparison = compare_metrics(baseline_metrics, metrics)

    schedule: Schedule | None = None
    run: OptimizationRun | None = None
    if persist:
        schedule, run = persist_result(
            session=session,
            instance=instance,
            result=result,
            metrics=metrics,
            validation=validation,
            request=request,
        )

    return RunOutcome(
        result=result,
        instance=instance,
        validation=validation,
        metrics=metrics,
        schedule=schedule,
        run=run,
        baseline_metrics=baseline_metrics,
        comparison=comparison,
    )


def persist_result(
    session: Session,
    instance: SchedulingInstance,
    result: ScheduleResult,
    metrics: dict[str, Any],
    validation: ValidationReport,
    request: RunRequest,
) -> tuple[Schedule, OptimizationRun]:
    """Store a run as a new schedule version plus its optimisation run."""
    code = f"PLAN-{datetime.now(UTC).strftime('%y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    name = request.name or f"{request.engine.upper()} plan {datetime.now(UTC):%Y-%m-%d %H:%M}"
    schedule = Schedule(
        code=code,
        name=name,
        version=1,
        status="draft",
        source=request.engine,
        horizon_start=instance.horizon_start,
        horizon_end=instance.horizon_end,
        scenario_id=request.scenario_id,
        parent_schedule_id=request.parent_schedule_id,
        is_published=False,
        created_by=request.actor,
        notes=request.notes,
        metrics=metrics,
        validation=validation.to_dict(),
    )
    session.add(schedule)
    session.flush()

    assignments = sorted(result.assignments, key=lambda a: (a.machine_id, a.start))
    for index, assignment in enumerate(assignments):
        work_order = session.get(WorkOrder, assignment.work_order_id)
        if work_order is None:
            continue
        session.add(
            ScheduleOperation(
                schedule_id=schedule.id,
                work_order_id=assignment.work_order_id,
                routing_operation_id=assignment.operation_id,
                machine_id=assignment.machine_id,
                operator_id=assignment.operator_id,
                starts_at=assignment.start,
                ends_at=assignment.end,
                setup_starts_at=assignment.setup_start,
                setup_minutes=assignment.setup_minutes,
                processing_minutes=assignment.processing_minutes,
                is_locked=assignment.is_locked,
                status="planned",
                sequence_index=index,
            )
        )

    run = OptimizationRun(
        schedule_id=schedule.id,
        engine=request.engine,
        status="completed" if result.status in {"OPTIMAL", "FEASIBLE"} else result.status.lower(),
        solver_status=result.status,
        objective_name=request.objective_name,
        objective_value=result.objective_value,
        best_bound=result.best_bound,
        runtime_ms=result.runtime_ms,
        parameters=result.parameters,
        metrics=metrics,
        validation=validation.to_dict(),
        diagnostics=[d.to_dict() for d in result.diagnostics],
        created_by=request.actor,
    )
    session.add(run)
    session.flush()

    audit.record(
        session,
        actor=request.actor,
        action=f"schedule.{request.engine}",
        entity_type="schedule",
        entity_id=schedule.id,
        details={
            "status": result.status,
            "operations": len(result.assignments),
            "valid": validation.valid,
            "objective_value": result.objective_value,
        },
    )
    return schedule, run


def load_assignments(session: Session, schedule_id: int) -> list[Assignment]:
    """Rebuild :class:`Assignment` objects from a persisted schedule."""
    schedule = session.get(Schedule, schedule_id)
    if schedule is None:
        raise SchedulingError(f"Schedule {schedule_id} does not exist.")
    rows = (
        session.scalars(
            select(ScheduleOperation)
            .where(ScheduleOperation.schedule_id == schedule_id)
            .order_by(ScheduleOperation.starts_at)
        )
    ).all()
    assignments: list[Assignment] = []
    for row in rows:
        work_order = session.get(WorkOrder, row.work_order_id)
        if work_order is None:
            continue
        operation = row.routing_operation
        machine = row.machine
        operator = row.operator
        assignments.append(
            Assignment(
                task_key=f"{work_order.code}:{operation.sequence}",
                work_order_id=row.work_order_id,
                work_order_code=work_order.code,
                operation_id=row.routing_operation_id,
                sequence=operation.sequence,
                name=operation.name,
                machine_id=row.machine_id or 0,
                machine_code=machine.code if machine else "",
                operator_id=row.operator_id,
                operator_code=operator.code if operator else None,
                start=ensure_utc(row.starts_at),
                end=ensure_utc(row.ends_at),
                processing_minutes=int(row.processing_minutes),
                setup_minutes=int(row.setup_minutes),
                setup_start=ensure_utc(row.setup_starts_at) if row.setup_starts_at else None,
                is_locked=row.is_locked,
                setup_family=operation.setup_family or work_order.product.family,
            )
        )
    return assignments


def load_reference(session: Session, schedule_id: int) -> dict[str, tuple[datetime, int | None]]:
    """Reference plan used by the schedule-stability objective."""
    reference: dict[str, tuple[datetime, int | None]] = {}
    for assignment in load_assignments(session, schedule_id):
        reference[assignment.task_key] = (assignment.start, assignment.machine_id)
    return reference


def publish_schedule(session: Session, schedule_id: int, actor: str) -> Schedule:
    """Publish a validated schedule and archive the previous publication."""
    schedule = session.get(Schedule, schedule_id)
    if schedule is None:
        raise SchedulingError(f"Schedule {schedule_id} does not exist.")
    validation = schedule.validation or {}
    if validation.get("valid") is False:
        raise SchedulingError(
            "Refusing to publish a schedule that fails validation: "
            + "; ".join(v.get("message", "") for v in validation.get("violations", [])[:3])
        )
    previous = session.scalars(
        select(Schedule).where(Schedule.is_published.is_(True), Schedule.id != schedule_id)
    ).all()
    for other in previous:
        other.is_published = False
        other.status = "superseded"
    schedule.is_published = True
    schedule.status = "published"
    schedule.published_at = datetime.now(UTC)
    audit.record(
        session,
        actor=actor,
        action="schedule.publish",
        entity_type="schedule",
        entity_id=schedule.id,
        details={"previous": [s.id for s in previous]},
    )
    session.flush()
    return schedule


def compare_schedules(session: Session, schedule_ids: Sequence[int]) -> dict[str, Any]:
    """Compare the persisted metrics of two or more schedules."""
    if len(schedule_ids) < 2:
        raise SchedulingError("At least two schedules are required for a comparison.")
    loaded = [session.get(Schedule, sid) for sid in schedule_ids]
    if any(s is None for s in loaded):
        raise SchedulingError("One of the schedules does not exist.")
    schedules: list[Schedule] = [s for s in loaded if s is not None]
    first = schedules[0].metrics or {}
    rows: list[dict[str, Any]] = []
    for schedule in schedules[1:]:
        comparison = compare_metrics(first, schedule.metrics or {})
        rows.append(
            {
                "baseline_schedule_id": schedules[0].id,
                "optimised_schedule_id": schedule.id,
                "rows": comparison["rows"],
                "improved_metrics": comparison["improved_metrics"],
                "regressed_metrics": comparison["regressed_metrics"],
            }
        )
    return {
        "schedules": [
            {
                "id": s.id,
                "code": s.code,
                "name": s.name,
                "source": s.source,
                "status": s.status,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "metrics": s.metrics,
            }
            for s in schedules
        ],
        "comparisons": rows,
    }
