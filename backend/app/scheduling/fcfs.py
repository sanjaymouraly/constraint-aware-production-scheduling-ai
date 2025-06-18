"""FCFS (first-come-first-served) baseline scheduler.

The baseline is the *reference point* of the whole platform, so it is
deliberately simple, deterministic and documented:

**Priority rule** (applied when ordering the work to schedule):

1. Higher order priority first (5 → 1).
2. Earlier hard deadline first (orders with a mandatory commitment go first).
3. Earlier due date first.
4. Earlier release date first.
5. Lexicographic work-order code (stable, reproducible tie-break).
6. Routing sequence ascending (operations of an order keep their sequence).

**Placement rule**: *earliest feasible placement*.  For every candidate
(machine, operator) pair the earliest wall-clock start is computed that

* is not before the release date and not before the predecessor finish plus
  the transfer time,
* fits entirely inside one working segment of the machine calendar (which
  already excludes maintenance windows and breakdowns),
* fits entirely inside one working segment of the operator calendar (which
  already excludes absences),
* does not overlap any operation already placed on that machine or assigned to
  that operator,
* leaves at least the sequence-dependent setup time between the operation and
  its time-neighbours on that machine,
* and has the required material available (time-phased inventory).

The baseline honours exactly the same hard constraints as the optimised
scheduler, so the two are directly comparable.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from app.domain.timeutils import Interval, add_minutes
from app.scheduling.instance import (
    MachineResource,
    OperatorResource,
    SchedulingInstance,
    Task,
)
from app.scheduling.result import Assignment, Diagnostic, ScheduleResult


def order_tasks(instance: SchedulingInstance) -> list[Task]:
    """Deterministic FCFS ordering (documented in the module docstring)."""

    def sort_key(task: Task) -> tuple:
        return (
            -task.priority,
            task.hard_deadline or task.due_at,
            task.due_at,
            task.release_at,
            task.work_order_code,
            task.sequence,
        )

    return sorted(instance.tasks, key=sort_key)


class _MachineState:
    """Bookings for one machine."""

    def __init__(self, machine: MachineResource) -> None:
        self.machine = machine
        self.assignments: list[Assignment] = []


class _OperatorState:
    def __init__(self, operator: OperatorResource) -> None:
        self.operator = operator
        self.intervals: list[Interval] = []

    def is_free(self, start: datetime, end: datetime) -> bool:
        probe = Interval(start, end)
        return not any(probe.overlaps(i) for i in self.intervals)


class FCFSScheduler:
    """Deterministic greedy baseline."""

    engine_name = "fcfs"

    def __init__(self, instance: SchedulingInstance) -> None:
        self.instance = instance
        self.machine_states = {
            mid: _MachineState(machine) for mid, machine in instance.machines.items()
        }
        self.operator_states = {oid: _OperatorState(op) for oid, op in instance.operators.items()}
        self.task_ends: dict[str, datetime] = {}
        self.material_usage: dict[int, list[tuple[datetime, float]]] = {}
        self.diagnostics: list[Diagnostic] = []

    # -- public -----------------------------------------------------------
    def solve(self) -> ScheduleResult:
        started = time.perf_counter()
        assignments: list[Assignment] = []
        unscheduled: list[str] = []

        for task in order_tasks(self.instance):
            placement = self._place(task)
            if placement is None:
                unscheduled.append(task.key)
                self.diagnostics.append(
                    Diagnostic(
                        code="task_unplaceable",
                        severity="error",
                        message=(
                            f"Task '{task.name}' of order {task.work_order_code} could not be "
                            f"placed within the planning horizon on any eligible machine."
                        ),
                        task_key=task.key,
                        details={
                            "eligible_machines": list(task.eligible_machine_ids),
                            "duration_minutes": task.duration_minutes,
                        },
                    )
                )
                continue
            assignments.append(placement)
            self._commit(task, placement)

        runtime_ms = (time.perf_counter() - started) * 1000.0
        status = "FEASIBLE" if not unscheduled else ("PARTIAL" if assignments else "INFEASIBLE")
        return ScheduleResult(
            engine=self.engine_name,
            status=status,  # type: ignore[arg-type]
            assignments=assignments,
            unscheduled=unscheduled,
            runtime_ms=runtime_ms,
            diagnostics=self.diagnostics,
            horizon_start=self.instance.horizon_start,
            horizon_end=self.instance.horizon_end,
            parameters={"ordering": "priority,hard_deadline,due,release,code,sequence"},
        )

    # -- internals --------------------------------------------------------
    def _earliest_start_for_task(self, task: Task) -> datetime:
        earliest = task.release_at
        for pred_key, transfer in task.transfer_after_predecessor:
            pred_end = self.task_ends.get(pred_key)
            if pred_end is not None:
                candidate = add_minutes(pred_end, transfer)
                if candidate > earliest:
                    earliest = candidate
        if task.locked and task.locked_start is not None:
            earliest = max(earliest, task.locked_start)
        return earliest

    def _place(self, task: Task) -> Assignment | None:
        """Earliest feasible placement across every (machine, operator) pair."""
        earliest = self._earliest_start_for_task(task)
        best: tuple[datetime, str, str] | None = None
        best_assignment: Assignment | None = None

        for machine in self.instance.eligible_machines(task):
            if task.locked and task.locked_machine_id is not None:
                if machine.id != task.locked_machine_id:
                    continue
            operators = [
                operator
                for operator in self.instance.eligible_operators(task, machine)
                if not (
                    task.locked
                    and task.locked_operator_id is not None
                    and operator.id != task.locked_operator_id
                )
            ]
            for operator in operators:
                start = self._earliest_slot(task, machine, operator, earliest)
                if start is None:
                    continue
                ready = self._material_ready_at(task, start)
                if ready is None:
                    continue
                if (
                    task.hard_deadline is not None
                    and add_minutes(ready, task.duration_minutes) > task.hard_deadline
                ):
                    continue
                if ready > start:
                    start = self._earliest_slot(task, machine, operator, ready)
                    if start is None:
                        continue
                if not self._materials_available(task, start):
                    continue
                key = (start, machine.code, operator.code)
                if best is not None and key >= best:
                    continue
                best = key
                best_assignment = self._build_assignment(task, machine, operator, start)
        return best_assignment

    def _build_assignment(
        self,
        task: Task,
        machine: MachineResource,
        operator: OperatorResource,
        start: datetime,
    ) -> Assignment:
        end = add_minutes(start, task.duration_minutes)
        setup = self._setup_before(task, machine, start)
        setup_start = add_minutes(start, -setup) if setup else None
        return Assignment(
            task_key=task.key,
            work_order_id=task.work_order_id,
            work_order_code=task.work_order_code,
            operation_id=task.operation_id,
            sequence=task.sequence,
            name=task.name,
            machine_id=machine.id,
            machine_code=machine.code,
            operator_id=operator.id,
            operator_code=operator.code,
            start=start,
            end=end,
            processing_minutes=task.duration_minutes,
            setup_minutes=setup,
            setup_start=setup_start,
            is_locked=task.locked,
            setup_family=task.setup_family,
        )

    def _earliest_slot(
        self,
        task: Task,
        machine: MachineResource,
        operator: OperatorResource,
        not_before: datetime,
    ) -> datetime | None:
        """Earliest start where machine *and* operator can run *task*."""
        duration = task.duration_minutes
        horizon_end = self.instance.horizon_end
        state = self.machine_states[machine.id]
        operator_state = self.operator_states[operator.id]

        candidates: set[datetime] = {not_before}
        for existing in state.assignments:
            candidates.add(
                add_minutes(
                    existing.end, machine.setup_for(existing.setup_family, task.setup_family)
                )
            )
            candidates.add(
                add_minutes(
                    existing.start,
                    -(duration + machine.setup_for(task.setup_family, existing.setup_family)),
                )
            )
        for segment in machine.availability.working:
            candidates.add(max(segment.start, not_before))
        for segment in operator.availability.working:
            candidates.add(max(segment.start, not_before))
        for interval in operator_state.intervals:
            candidates.add(interval.end)

        deadline = task.hard_deadline
        for moment in sorted(candidates):
            if moment < not_before:
                continue
            end = add_minutes(moment, duration)
            if end > horizon_end:
                continue
            if deadline is not None and end > deadline:
                continue  # a mandatory deadline is a hard constraint
            if not machine.availability.contains(moment, end):
                continue
            if not operator.availability.contains(moment, end):
                continue
            if any(
                existing.occupancy.overlaps(Interval(moment, end)) for existing in state.assignments
            ):
                continue
            if not operator_state.is_free(moment, end):
                continue
            if not self._setup_gaps_ok(task, machine, moment):
                continue
            return moment
        return None

    def _neighbours(self, machine: MachineResource, moment: datetime):
        """Time-neighbours of *moment* on the machine (previous / next op)."""
        state = self.machine_states[machine.id]
        previous = None
        following = None
        for existing in state.assignments:
            if existing.end <= moment:
                if previous is None or existing.end > previous.end:
                    previous = existing
            elif existing.start >= moment:
                if following is None or existing.start < following.start:
                    following = existing
        return previous, following

    def _setup_gaps_ok(self, task: Task, machine: MachineResource, moment: datetime) -> bool:
        previous, following = self._neighbours(machine, moment)
        end = add_minutes(moment, task.duration_minutes)
        if previous is not None:
            required = machine.setup_for(previous.setup_family, task.setup_family)
            if moment < add_minutes(previous.end, required):
                return False
        if following is not None:
            required = machine.setup_for(task.setup_family, following.setup_family)
            if following.start < add_minutes(end, required):
                return False
        return True

    def _setup_before(self, task: Task, machine: MachineResource, moment: datetime) -> int:
        previous, _ = self._neighbours(machine, moment)
        if previous is None:
            return 0
        return machine.setup_for(previous.setup_family, task.setup_family)

    def _material_ready_at(self, task: Task, not_before: datetime) -> datetime | None:
        """Earliest instant at which every material of *task* is available.

        Inventory is time-phased: an operation can only start when the stock
        that is physically on hand (plus confirmed receipts) covers everything
        that has already been consumed.
        """
        ready = not_before
        for material_id, quantity in task.material_requirements:
            stock = self.instance.materials.get(material_id)
            if stock is None:
                return None
            usage = self.material_usage.get(material_id, [])
            epochs: list[tuple[datetime, float]] = [
                (max(self.instance.horizon_start, not_before), stock.on_hand)
            ]
            running = stock.on_hand
            for arrival, received in stock.receipts:
                running += received
                epochs.append((arrival, running))
            found: datetime | None = None
            for index, (epoch_start, available) in enumerate(epochs):
                if index + 1 < len(epochs):
                    epoch_end = epochs[index + 1][0] - timedelta(minutes=1)
                else:
                    epoch_end = self.instance.horizon_end
                consumed = sum(qty for moment, qty in usage if moment <= epoch_end)
                if consumed + quantity <= available + 1e-6:
                    candidate = max(not_before, epoch_start)
                    if candidate <= epoch_end:
                        found = candidate
                        break
            if found is None:
                return None
            ready = max(ready, found)
        return ready

    def _materials_available(self, task: Task, start: datetime) -> bool:
        for material_id, quantity in task.material_requirements:
            stock = self.instance.materials.get(material_id)
            if stock is None:
                return False
            available = stock.available_at(start)
            consumed = sum(
                qty for moment, qty in self.material_usage.get(material_id, []) if moment <= start
            )
            if consumed + quantity > available + 1e-6:
                self.diagnostics.append(
                    Diagnostic(
                        code="material_shortage",
                        severity="error",
                        message=(
                            f"Material '{stock.sku}' is short for task '{task.name}' "
                            f"(order {task.work_order_code}) at {start.isoformat()}."
                        ),
                        task_key=task.key,
                        material_id=material_id,
                        details={
                            "required": quantity,
                            "available": available,
                            "already_consumed": consumed,
                        },
                    )
                )
                return False
        return True

    def _commit(self, task: Task, assignment: Assignment) -> None:
        self.machine_states[assignment.machine_id].assignments.append(assignment)
        if assignment.operator_id is not None:
            self.operator_states[assignment.operator_id].intervals.append(
                Interval(assignment.start, assignment.end)
            )
        self.task_ends[task.key] = assignment.end
        for material_id, quantity in task.material_requirements:
            self.material_usage.setdefault(material_id, []).append((assignment.start, quantity))


def run_fcfs(instance: SchedulingInstance) -> ScheduleResult:
    return FCFSScheduler(instance).solve()
