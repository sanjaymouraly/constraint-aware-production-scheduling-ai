"""Schedule metrics.

Every number reported by the platform is computed here from the *actual*
schedule, so the FCFS baseline and the optimised schedule are always measured
with the same definitions and the same denominators.

Definitions
-----------
``on_time_delivery_pct``
    Share of **scheduled** work orders whose last operation finishes at or
    before the order's due date.  The denominator is the number of scheduled
    orders (unscheduled orders are excluded and reported separately).
``weighted_tardiness``
    Sum of ``priority × tardiness`` over orders (priority 1..5, tardiness in
    minutes).
``makespan_minutes``
    ``last operation end − first operation start`` of the whole plan.
``machine_utilisation``
    Busy machine minutes divided by the machine's **available working minutes
    inside the planning horizon** (not by the length of the calendar
    interval), which is the only denominator that reflects real capacity.
``overtime_minutes``
    Operator minutes assigned beyond the contracted regular capacity of the
    horizon.
``setup_minutes``
    Sum of the sequence-dependent setup gaps between time-consecutive
    operations on every machine.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from app.scheduling.instance import SchedulingInstance
from app.scheduling.result import Assignment


def _round(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def compute_metrics(
    instance: SchedulingInstance,
    assignments: list[Assignment],
    *,
    reference: dict[str, tuple[datetime, int | None]] | None = None,
    solver_runtime_ms: float | None = None,
) -> dict[str, Any]:
    """Compute the full KPI set for a schedule."""
    tasks = {t.key: t for t in instance.tasks}
    orders: dict[int, list[Assignment]] = defaultdict(list)
    for assignment in assignments:
        orders[assignment.work_order_id].append(assignment)

    # --- delivery performance -------------------------------------------
    on_time = 0
    late = 0
    tardiness_minutes = 0.0
    weighted_tardiness = 0.0
    penalty_cost = 0.0
    order_rows: list[dict[str, Any]] = []
    for order_id, order_assignments in orders.items():
        task = tasks[order_assignments[0].task_key]
        completion = max(a.end for a in order_assignments)
        late_minutes = max(0.0, (completion - task.due_at).total_seconds() / 60.0)
        is_late = completion > task.due_at
        if is_late:
            late += 1
            tardiness_minutes += late_minutes
            weighted_tardiness += task.priority * late_minutes
            penalty_cost += task.penalty_per_hour * (late_minutes / 60.0)
        else:
            on_time += 1
        order_rows.append(
            {
                "work_order_id": order_id,
                "work_order_code": task.work_order_code,
                "priority": task.priority,
                "due_at": task.due_at.isoformat(),
                "completion": completion.isoformat(),
                "tardiness_minutes": _round(late_minutes, 1),
                "is_late": is_late,
                "hard_deadline": task.hard_deadline.isoformat() if task.hard_deadline else None,
            }
        )
    scheduled_orders = len(orders)
    on_time_pct = (on_time / scheduled_orders * 100.0) if scheduled_orders else 0.0

    # --- makespan / throughput -------------------------------------------
    if assignments:
        first_start = min(a.start for a in assignments)
        last_end = max(a.end for a in assignments)
        makespan = int((last_end - first_start).total_seconds() // 60)
    else:
        first_start = instance.horizon_start
        last_end = instance.horizon_start
        makespan = 0

    horizon_days = max(
        1.0, (instance.horizon_end - instance.horizon_start).total_seconds() / 86400.0
    )
    units_completed = 0.0
    units_scheduled = 0.0
    for order_id, order_assignments in orders.items():
        task = tasks[order_assignments[0].task_key]
        units_scheduled += 1.0
        completion = max(a.end for a in order_assignments)
        if completion <= instance.horizon_end:
            units_completed += 1.0

    # --- machine utilisation ---------------------------------------------
    busy_by_machine: dict[int, float] = defaultdict(float)
    for assignment in assignments:
        busy_by_machine[assignment.machine_id] += assignment.processing_minutes
    setup_by_machine: dict[int, float] = defaultdict(float)
    for assignment in assignments:
        setup_by_machine[assignment.machine_id] += assignment.setup_minutes

    machine_rows: list[dict[str, Any]] = []
    total_busy = 0.0
    total_available = 0.0
    for machine_id, machine in instance.machines.items():
        busy = busy_by_machine.get(machine_id, 0.0)
        available = float(machine.availability.total_working_minutes)
        total_busy += busy
        total_available += available
        machine_rows.append(
            {
                "machine_id": machine_id,
                "machine_code": machine.code,
                "machine_name": machine.name,
                "machine_type": machine.machine_type,
                "busy_minutes": _round(busy, 1),
                "setup_minutes": _round(setup_by_machine.get(machine_id, 0.0), 1),
                "available_minutes": _round(available, 1),
                "utilisation_pct": _round(busy / available * 100.0, 2) if available else 0.0,
                "operations": sum(1 for a in assignments if a.machine_id == machine_id),
            }
        )
    machine_rows.sort(key=lambda r: (-r["utilisation_pct"], r["machine_code"]))

    # --- labour -----------------------------------------------------------
    minutes_by_operator: dict[int, float] = defaultdict(float)
    for assignment in assignments:
        if assignment.operator_id is not None:
            minutes_by_operator[assignment.operator_id] += assignment.processing_minutes

    operator_rows: list[dict[str, Any]] = []
    total_overtime = 0.0
    total_labour_cost = 0.0
    for operator_id, operator in instance.operators.items():
        assigned = minutes_by_operator.get(operator_id, 0.0)
        regular_capacity = operator.weekly_regular_minutes * (horizon_days / 7.0)
        overtime = max(0.0, assigned - regular_capacity)
        total_overtime += overtime
        total_labour_cost += (assigned / 60.0) * operator.hourly_cost
        operator_rows.append(
            {
                "operator_id": operator_id,
                "operator_code": operator.code,
                "operator_name": operator.name,
                "assigned_minutes": _round(assigned, 1),
                "regular_capacity_minutes": _round(regular_capacity, 1),
                "overtime_minutes": _round(overtime, 1),
                "operations": sum(1 for a in assignments if a.operator_id == operator_id),
            }
        )
    operator_rows.sort(key=lambda r: (-r["overtime_minutes"], r["operator_code"]))

    # --- machine cost -----------------------------------------------------
    machine_cost = 0.0
    for machine_id, machine in instance.machines.items():
        busy = busy_by_machine.get(machine_id, 0.0)
        machine_cost += (busy / 60.0) * machine.hourly_cost

    total_setup = sum(setup_by_machine.values())

    # --- schedule stability ------------------------------------------------
    displaced = 0
    moved = 0
    if reference is not None:
        current = {a.task_key: (a.start, a.machine_id) for a in assignments}
        for key, (ref_start, ref_machine_id) in reference.items():
            other = current.get(key)
            if other is None:
                displaced += 1
            elif other[0] != ref_start or other[1] != ref_machine_id:
                moved += 1

    total_operations = len(instance.tasks) or 1

    metrics: dict[str, Any] = {
        "on_time_delivery_pct": _round(on_time_pct, 2),
        "orders_scheduled": scheduled_orders,
        "orders_on_time": on_time,
        "orders_late": late,
        "tardiness_minutes": _round(tardiness_minutes, 1),
        "tardiness_hours": _round(tardiness_minutes / 60.0, 2),
        "weighted_tardiness": _round(weighted_tardiness, 1),
        "penalty_cost": _round(penalty_cost, 2),
        "makespan_minutes": makespan,
        "makespan_hours": _round(makespan / 60.0, 2),
        "plan_start": first_start.isoformat(),
        "plan_end": last_end.isoformat(),
        "operations_scheduled": len(assignments),
        "operations_total": len(instance.tasks),
        "throughput_orders": _round(units_completed, 1),
        "setup_minutes": _round(total_setup, 1),
        "setup_hours": _round(total_setup / 60.0, 2),
        "machine_busy_minutes": _round(total_busy, 1),
        "machine_available_minutes": _round(total_available, 1),
        "machine_utilisation_pct": _round(total_busy / total_available * 100.0, 2)
        if total_available
        else 0.0,
        "overtime_minutes": _round(total_overtime, 1),
        "overtime_hours": _round(total_overtime / 60.0, 2),
        "labour_cost": _round(total_labour_cost, 2),
        "machine_cost": _round(machine_cost, 2),
        "operating_cost": _round(machine_cost + total_labour_cost, 2),
        "displaced_operations": displaced,
        "moved_operations": moved,
        "schedule_disruption_pct": _round((displaced + moved) / total_operations * 100.0, 2),
        "machines": machine_rows,
        "operators": operator_rows,
        "orders": order_rows,
        "bottleneck_machine": machine_rows[0] if machine_rows else None,
    }
    if solver_runtime_ms is not None:
        metrics["solver_runtime_ms"] = _round(solver_runtime_ms, 1)
    return metrics


def compare_metrics(baseline: dict[str, Any], optimised: dict[str, Any]) -> dict[str, Any]:
    """Compare two metric sets.

    Reports absolute values, the absolute difference and the relative change
    (only where the baseline is non-zero).  Metrics where the optimised run is
    **worse** are explicitly flagged instead of being hidden.
    """
    direction = {
        "on_time_delivery_pct": "up",
        "weighted_tardiness": "down",
        "tardiness_minutes": "down",
        "makespan_minutes": "down",
        "setup_minutes": "down",
        "overtime_minutes": "down",
        "machine_utilisation_pct": "up",
        "operating_cost": "down",
        "schedule_disruption_pct": "down",
        "penalty_cost": "down",
    }
    rows: list[dict[str, Any]] = []
    improvements = 0
    regressions = 0
    for key, better in direction.items():
        base = float(baseline.get(key, 0.0) or 0.0)
        opt = float(optimised.get(key, 0.0) or 0.0)
        delta = opt - base
        pct = (delta / base * 100.0) if base else None
        if better == "up":
            better_result = delta > 1e-9
            equal = abs(delta) <= 1e-9
        else:
            better_result = delta < -1e-9
            equal = abs(delta) <= 1e-9
        if better_result:
            improvements += 1
        elif not equal:
            regressions += 1
        rows.append(
            {
                "metric": key,
                "baseline": _round(base, 3),
                "optimised": _round(opt, 3),
                "delta": _round(delta, 3),
                "delta_pct": _round(pct, 2) if pct is not None else None,
                "direction": better,
                "verdict": "better" if better_result else ("equal" if equal else "worse"),
            }
        )
    return {
        "rows": rows,
        "improved_metrics": improvements,
        "regressed_metrics": regressions,
        "comparable": True,
        "note": (
            "Metrics are computed from the two validated schedules with identical "
            "definitions and denominators."
        ),
    }


def resource_timeline(
    instance: SchedulingInstance, assignments: Iterable[Assignment]
) -> dict[int, list[dict[str, Any]]]:
    """Per-resource timeline used by the Gantt chart."""
    timeline: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for assignment in assignments:
        timeline[assignment.machine_id].append(
            {
                "task_key": assignment.task_key,
                "work_order_code": assignment.work_order_code,
                "name": assignment.name,
                "start": assignment.start.isoformat(),
                "end": assignment.end.isoformat(),
                "setup_minutes": assignment.setup_minutes,
                "operator_code": assignment.operator_code,
                "is_locked": assignment.is_locked,
            }
        )
    return dict(timeline)
