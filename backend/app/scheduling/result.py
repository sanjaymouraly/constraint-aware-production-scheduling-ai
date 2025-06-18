"""Solver result structures shared by the FCFS and CP-SAT engines."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.domain.timeutils import Interval

SolverStatus = Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "ERROR", "CANCELLED"]


@dataclass
class Assignment:
    """A scheduled operation."""

    task_key: str
    work_order_id: int
    work_order_code: str
    operation_id: int
    sequence: int
    name: str
    machine_id: int
    machine_code: str
    operator_id: int | None
    operator_code: str | None
    start: datetime
    end: datetime
    processing_minutes: int
    setup_minutes: int = 0
    setup_start: datetime | None = None
    is_locked: bool = False
    setup_family: str = ""

    @property
    def occupancy(self) -> Interval:
        return Interval(self.start, self.end)


@dataclass
class Diagnostic:
    code: str
    severity: str  # error | warning | info
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
class ScheduleResult:
    """Outcome of one scheduling run."""

    engine: str
    status: SolverStatus
    assignments: list[Assignment] = field(default_factory=list)
    unscheduled: list[str] = field(default_factory=list)
    objective_value: float | None = None
    best_bound: float | None = None
    runtime_ms: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    solver_log: str = ""
    horizon_start: datetime | None = None
    horizon_end: datetime | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def is_feasible(self) -> bool:
        return self.status in {"OPTIMAL", "FEASIBLE", "CANCELLED"} and not self.unscheduled

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "status": self.status,
            "objective_value": self.objective_value,
            "best_bound": self.best_bound,
            "runtime_ms": self.runtime_ms,
            "metrics": self.metrics,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "unscheduled": list(self.unscheduled),
            "assignments": [
                {
                    "task_key": a.task_key,
                    "work_order_id": a.work_order_id,
                    "work_order_code": a.work_order_code,
                    "operation_id": a.operation_id,
                    "sequence": a.sequence,
                    "name": a.name,
                    "machine_id": a.machine_id,
                    "machine_code": a.machine_code,
                    "operator_id": a.operator_id,
                    "operator_code": a.operator_code,
                    "start": a.start.isoformat(),
                    "end": a.end.isoformat(),
                    "processing_minutes": a.processing_minutes,
                    "setup_minutes": a.setup_minutes,
                    "setup_start": a.setup_start.isoformat() if a.setup_start else None,
                    "is_locked": a.is_locked,
                    "setup_family": a.setup_family,
                }
                for a in self.assignments
            ],
        }
