"""Independent schedule validator.

This module deliberately does **not** reuse the solver code: it re-derives
every hard constraint from the :class:`SchedulingInstance` and checks the
produced schedule from scratch.  A schedule is only considered publishable
when the validator reports zero violations.

Hard constraints checked (see ``docs/ARCHITECTURE.md``):

===  ============================================================
H1   machine eligibility (type, capability, product family)
H2   machine capacity (no overlapping operations)
H3   machine working calendar (incl. maintenance/breakdown exclusion)
H4   sequence dependent setup gaps between time-consecutive operations
H5   operator qualification (skills / certifications)
H6   operator machine eligibility
H7   operator availability and no double booking
H8   material availability (time-phased inventory)
H9   release dates
H10  routing precedence including transfer times
H11  mandatory (hard) deadlines
H12  locked operations keep their committed slot
H13  operation durations / indivisibility
H14  planning horizon
===  ============================================================
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.timeutils import add_minutes
from app.scheduling.instance import SchedulingInstance
from app.scheduling.result import Assignment

#: The hard constraints audited by this module (see the module docstring).
CONSTRAINT_CODES: tuple[tuple[str, str], ...] = (
    ("H1", "machine_eligibility"),
    ("H2", "machine_capacity"),
    ("H3", "working_calendar"),
    ("H4", "setup_gap"),
    ("H5", "operator_qualification"),
    ("H6", "operator_eligibility"),
    ("H7", "operator_availability"),
    ("H8", "material_availability"),
    ("H9", "release_date"),
    ("H10", "precedence"),
    ("H11", "hard_deadline"),
    ("H12", "locked_operations"),
    ("H13", "duration"),
    ("H14", "horizon"),
)


@dataclass
class Violation:
    code: str
    severity: str  # error | warning
    message: str
    task_key: str | None = None
    machine_id: int | None = None
    operator_id: int | None = None
    material_id: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "task_key": self.task_key,
            "machine_id": self.machine_id,
            "operator_id": self.operator_id,
            "material_id": self.material_id,
            "details": self.details,
        }


@dataclass
class ValidationReport:
    valid: bool
    violations: list[Violation] = field(default_factory=list)
    checked_operations: int = 0
    checked_constraints: list[str] = field(default_factory=list)

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "warning"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "checked_operations": self.checked_operations,
            "violation_count": len(self.violations),
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "violations": [v.to_dict() for v in self.violations],
            "checked_constraints": self.checked_constraints,
        }


def validate_schedule(
    instance: SchedulingInstance,
    assignments: list[Assignment],
    *,
    require_complete: bool = True,
) -> ValidationReport:
    """Audit *assignments* against every hard constraint of *instance*."""
    violations: list[Violation] = []
    tasks = {t.key: t for t in instance.tasks}

    # ---- completeness ------------------------------------------------
    scheduled: dict[str, list[Assignment]] = defaultdict(list)
    for assignment in assignments:
        scheduled[assignment.task_key].append(assignment)
    for key, task in tasks.items():
        count = len(scheduled.get(key, []))
        if count == 0:
            if require_complete:
                violations.append(
                    Violation(
                        code="task_not_scheduled",
                        severity="error",
                        message=(
                            f"Operation '{task.name}' of order {task.work_order_code} is not "
                            f"scheduled."
                        ),
                        task_key=key,
                    )
                )
        elif count > 1:
            violations.append(
                Violation(
                    code="task_scheduled_twice",
                    severity="error",
                    message=(
                        f"Operation '{task.name}' of order {task.work_order_code} is scheduled "
                        f"{count} times."
                    ),
                    task_key=key,
                )
            )

    by_machine: dict[int, list[Assignment]] = defaultdict(list)
    by_operator: dict[int, list[Assignment]] = defaultdict(list)
    for assignment in assignments:
        by_machine[assignment.machine_id].append(assignment)
        if assignment.operator_id is not None:
            by_operator[assignment.operator_id].append(assignment)

    # ---- per assignment checks ----------------------------------------
    for assignment in assignments:
        found_task = tasks.get(assignment.task_key)
        if found_task is None:
            violations.append(
                Violation(
                    code="unknown_task",
                    severity="error",
                    message=f"Assignment references unknown task '{assignment.task_key}'.",
                    task_key=assignment.task_key,
                )
            )
            continue
        task = found_task

        # H14 horizon
        if assignment.start < instance.horizon_start or assignment.end > instance.horizon_end:
            violations.append(
                Violation(
                    code="outside_horizon",
                    severity="error",
                    message=(
                        f"Operation '{task.name}' runs outside the planning horizon "
                        f"({assignment.start.isoformat()} → {assignment.end.isoformat()})."
                    ),
                    task_key=task.key,
                )
            )

        # H13 duration
        actual = int((assignment.end - assignment.start).total_seconds() // 60)
        if actual != task.duration_minutes:
            violations.append(
                Violation(
                    code="duration_mismatch",
                    severity="error",
                    message=(
                        f"Operation '{task.name}' is planned for {actual} minutes but "
                        f"{task.duration_minutes} minutes are required."
                    ),
                    task_key=task.key,
                    details={"planned": actual, "required": task.duration_minutes},
                )
            )

        # H1 machine eligibility
        machine = instance.machines.get(assignment.machine_id)
        if machine is None:
            violations.append(
                Violation(
                    code="unknown_machine",
                    severity="error",
                    message=f"Operation '{task.name}' references an unknown machine.",
                    task_key=task.key,
                    machine_id=assignment.machine_id,
                )
            )
        else:
            if assignment.machine_id not in task.eligible_machine_ids:
                violations.append(
                    Violation(
                        code="machine_not_eligible",
                        severity="error",
                        message=(
                            f"Machine {machine.code} is not eligible for operation '{task.name}'."
                        ),
                        task_key=task.key,
                        machine_id=machine.id,
                    )
                )
            if machine.machine_type != task.machine_type:
                violations.append(
                    Violation(
                        code="machine_type_mismatch",
                        severity="error",
                        message=(
                            f"Machine {machine.code} is of type '{machine.machine_type}' but "
                            f"operation '{task.name}' requires '{task.machine_type}'."
                        ),
                        task_key=task.key,
                        machine_id=machine.id,
                    )
                )
            if machine.supported_families and task.setup_family not in machine.supported_families:
                violations.append(
                    Violation(
                        code="machine_capability_mismatch",
                        severity="error",
                        message=(
                            f"Machine {machine.code} does not support product family "
                            f"'{task.setup_family}' required by '{task.name}'."
                        ),
                        task_key=task.key,
                        machine_id=machine.id,
                    )
                )
            # H3 calendar
            if not machine.availability.contains(assignment.start, assignment.end):
                violations.append(
                    Violation(
                        code="outside_working_calendar",
                        severity="error",
                        message=(
                            f"Operation '{task.name}' on {machine.code} is not inside a single "
                            f"working window of the machine calendar."
                        ),
                        task_key=task.key,
                        machine_id=machine.id,
                        details={
                            "start": assignment.start.isoformat(),
                            "end": assignment.end.isoformat(),
                        },
                    )
                )

        # H5/H6/H7 operator
        if task.crew_size >= 1:
            if assignment.operator_id is None:
                violations.append(
                    Violation(
                        code="no_operator_assigned",
                        severity="error",
                        message=f"Operation '{task.name}' has no operator assigned.",
                        task_key=task.key,
                    )
                )
            else:
                operator = instance.operators.get(assignment.operator_id)
                if operator is None:
                    violations.append(
                        Violation(
                            code="unknown_operator",
                            severity="error",
                            message=f"Operation '{task.name}' references an unknown operator.",
                            task_key=task.key,
                            operator_id=assignment.operator_id,
                        )
                    )
                else:
                    missing = set(task.required_skills) - set(operator.skills)
                    if missing:
                        violations.append(
                            Violation(
                                code="operator_not_qualified",
                                severity="error",
                                message=(
                                    f"Operator {operator.code} lacks the required skills "
                                    f"({', '.join(sorted(missing))}) for '{task.name}'."
                                ),
                                task_key=task.key,
                                operator_id=operator.id,
                            )
                        )
                    if machine is not None and not operator.eligible_for_machine(machine):
                        violations.append(
                            Violation(
                                code="operator_not_eligible_for_machine",
                                severity="error",
                                message=(
                                    f"Operator {operator.code} is not eligible to run "
                                    f"{machine.code} for operation '{task.name}'."
                                ),
                                task_key=task.key,
                                operator_id=operator.id,
                                machine_id=machine.id,
                            )
                        )
                    if not operator.availability.contains(assignment.start, assignment.end):
                        violations.append(
                            Violation(
                                code="operator_unavailable",
                                severity="error",
                                message=(
                                    f"Operator {operator.code} is not available for the whole "
                                    f"window of operation '{task.name}'."
                                ),
                                task_key=task.key,
                                operator_id=operator.id,
                            )
                        )

        # H9 release date
        if assignment.start < task.release_at:
            violations.append(
                Violation(
                    code="released_too_early",
                    severity="error",
                    message=(
                        f"Operation '{task.name}' starts before the order release date "
                        f"({task.release_at.isoformat()})."
                    ),
                    task_key=task.key,
                )
            )

        # H11 hard deadline
        if task.hard_deadline is not None and assignment.end > task.hard_deadline:
            violations.append(
                Violation(
                    code="hard_deadline_missed",
                    severity="error",
                    message=(
                        f"Operation '{task.name}' finishes after the mandatory deadline "
                        f"({task.hard_deadline.isoformat()})."
                    ),
                    task_key=task.key,
                    details={
                        "end": assignment.end.isoformat(),
                        "deadline": task.hard_deadline.isoformat(),
                    },
                )
            )

        # H12 locked operations
        if task.locked and task.locked_start is not None and task.locked_end is not None:
            if assignment.start != task.locked_start or assignment.end != task.locked_end:
                violations.append(
                    Violation(
                        code="locked_operation_moved",
                        severity="error",
                        message=(
                            f"Operation '{task.name}' is locked to "
                            f"{task.locked_start.isoformat()} → {task.locked_end.isoformat()} but "
                            f"was scheduled elsewhere."
                        ),
                        task_key=task.key,
                    )
                )
            if (
                task.locked_machine_id is not None
                and assignment.machine_id != task.locked_machine_id
            ):
                violations.append(
                    Violation(
                        code="locked_machine_changed",
                        severity="error",
                        message=f"Operation '{task.name}' is locked to a different machine.",
                        task_key=task.key,
                    )
                )

    # ---- H2 machine capacity ------------------------------------------
    for machine_id, machine_assignments in by_machine.items():
        ordered = sorted(machine_assignments, key=lambda a: a.start)
        for i in range(len(ordered) - 1):
            current = ordered[i]
            following = ordered[i + 1]
            if current.end > following.start:
                violations.append(
                    Violation(
                        code="machine_overlap",
                        severity="error",
                        message=(
                            f"Machine {current.machine_code} runs overlapping operations "
                            f"'{current.name}' and '{following.name}'."
                        ),
                        task_key=following.task_key,
                        machine_id=machine_id,
                        details={
                            "first_end": current.end.isoformat(),
                            "second_start": following.start.isoformat(),
                        },
                    )
                )
            else:
                # H4 setup gap between time-consecutive operations
                machine = instance.machines.get(machine_id)
                if machine is not None:
                    required = machine.setup_for(current.setup_family, following.setup_family)
                    gap = int((following.start - current.end).total_seconds() // 60)
                    if gap < required:
                        violations.append(
                            Violation(
                                code="setup_gap_too_short",
                                severity="error",
                                message=(
                                    f"Machine {machine.code} needs {required} min of setup between "
                                    f"'{current.name}' and '{following.name}' but only {gap} min "
                                    f"are available."
                                ),
                                task_key=following.task_key,
                                machine_id=machine_id,
                                details={"required": required, "available": gap},
                            )
                        )

    # ---- H7 operator double booking -----------------------------------
    for operator_id, operator_assignments in by_operator.items():
        ordered = sorted(operator_assignments, key=lambda a: a.start)
        for i in range(len(ordered) - 1):
            current = ordered[i]
            following = ordered[i + 1]
            if current.end > following.start:
                violations.append(
                    Violation(
                        code="operator_double_booked",
                        severity="error",
                        message=(
                            f"Operator {current.operator_code} is booked for overlapping "
                            f"operations '{current.name}' and '{following.name}'."
                        ),
                        task_key=following.task_key,
                        operator_id=operator_id,
                    )
                )

    # ---- H10 precedence -------------------------------------------------
    ends = {a.task_key: a.end for a in assignments}
    starts = {a.task_key: a.start for a in assignments}
    for task in instance.tasks:
        for pred_key, transfer in task.transfer_after_predecessor:
            if pred_key not in ends or task.key not in starts:
                continue
            earliest = add_minutes(ends[pred_key], transfer)
            if starts[task.key] < earliest:
                violations.append(
                    Violation(
                        code="precedence_violated",
                        severity="error",
                        message=(
                            f"Operation '{task.name}' starts at {starts[task.key].isoformat()} but "
                            f"its predecessor finishes at {ends[pred_key].isoformat()} "
                            f"(+{transfer} min transfer)."
                        ),
                        task_key=task.key,
                        details={
                            "predecessor": pred_key,
                            "required_start": earliest.isoformat(),
                        },
                    )
                )

    # ---- H8 material availability ---------------------------------------
    violations.extend(_validate_materials(instance, assignments))

    report = ValidationReport(
        valid=not any(v.severity == "error" for v in violations),
        violations=violations,
        checked_operations=len(assignments),
        checked_constraints=[f"{code} {name}" for code, name in CONSTRAINT_CODES],
    )
    return report


def _validate_materials(
    instance: SchedulingInstance, assignments: list[Assignment]
) -> list[Violation]:
    violations: list[Violation] = []
    consumption: dict[int, list[tuple[datetime, float, str]]] = defaultdict(list)
    for assignment in assignments:
        task = next((t for t in instance.tasks if t.key == assignment.task_key), None)
        if task is None:
            continue
        for material_id, quantity in task.material_requirements:
            if quantity <= 0:
                continue
            consumption[material_id].append((assignment.start, quantity, assignment.task_key))

    for material_id, entries in consumption.items():
        stock = instance.materials.get(material_id)
        if stock is None:
            violations.append(
                Violation(
                    code="unknown_material",
                    severity="error",
                    message=f"Material {material_id} referenced by the schedule does not exist.",
                    material_id=material_id,
                )
            )
            continue
        entries.sort(key=lambda e: e[0])
        running = 0.0
        for start, quantity, task_key in entries:
            available = stock.available_at(start)
            running += quantity
            if running > available + 1e-6:
                violations.append(
                    Violation(
                        code="material_shortage",
                        severity="error",
                        message=(
                            f"Material '{stock.sku}' is short at {start.isoformat()}: "
                            f"{running:.2f} required but only {available:.2f} available."
                        ),
                        task_key=task_key,
                        material_id=material_id,
                        details={
                            "consumed": running,
                            "available": available,
                            "required_now": quantity,
                        },
                    )
                )
                break
    return violations
