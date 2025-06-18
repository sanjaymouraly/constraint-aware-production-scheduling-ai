"""Scheduling entities: schedules, schedule operations, runs, scenarios, jobs, audit."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime, utcnow

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.catalog import RoutingOperation, WorkOrder
    from app.models.resources import Machine, Operator


class Scenario(Base):
    """A what-if scenario built on top of a published schedule."""

    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    base_schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False, index=True)
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    __table_args__ = (CheckConstraint("status IN ('draft','simulated','published','archived')"),)


class Schedule(Base):
    """An immutable-ish version of a production plan."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(24), default="fcfs", nullable=False)
    horizon_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    horizon_end: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    scenario_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenarios.id", ondelete="SET NULL"), index=True
    )
    parent_schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), index=True
    )
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    operations: Mapped[list[ScheduleOperation]] = relationship(
        back_populates="schedule",
        cascade="all, delete-orphan",
        order_by="ScheduleOperation.starts_at",
    )
    runs: Mapped[list[OptimizationRun]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_schedule_code_version"),
        CheckConstraint(
            "status IN ('draft','published','archived','superseded','rejected','partial')"
        ),
        CheckConstraint("source IN ('fcfs','cpsat','manual','imported')"),
        CheckConstraint("horizon_end > horizon_start"),
    )


class ScheduleOperation(Base):
    """A scheduled assignment of a routing operation to a machine/operator."""

    __tablename__ = "schedule_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    work_order_id: Mapped[int] = mapped_column(
        ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    routing_operation_id: Mapped[int] = mapped_column(
        ForeignKey("routing_operations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    machine_id: Mapped[int | None] = mapped_column(
        ForeignKey("machines.id", ondelete="RESTRICT"), index=True
    )
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("operators.id", ondelete="RESTRICT"), index=True
    )
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    setup_starts_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    setup_minutes: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    processing_minutes: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="planned", nullable=False)
    sequence_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    schedule: Mapped[Schedule] = relationship(back_populates="operations")
    work_order: Mapped[WorkOrder] = relationship()
    routing_operation: Mapped[RoutingOperation] = relationship()
    machine: Mapped[Machine | None] = relationship()
    operator: Mapped[Operator | None] = relationship()

    __table_args__ = (
        CheckConstraint("ends_at > starts_at"),
        CheckConstraint("processing_minutes >= 0"),
        CheckConstraint("setup_minutes >= 0"),
        Index("ix_schedule_ops_schedule_start", "schedule_id", "starts_at"),
        Index("ix_schedule_ops_machine_start", "machine_id", "starts_at"),
    )


class OptimizationRun(Base):
    """Record of one optimisation / baseline execution."""

    __tablename__ = "optimization_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), index=True
    )
    engine: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False, index=True)
    #: OPTIMAL | FEASIBLE | INFEASIBLE | UNKNOWN | ERROR
    solver_status: Mapped[str | None] = mapped_column(String(24))
    objective_name: Mapped[str | None] = mapped_column(String(64))
    objective_value: Mapped[float | None] = mapped_column(Float)
    best_bound: Mapped[float | None] = mapped_column(Float)
    runtime_ms: Mapped[float | None] = mapped_column(Float)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    diagnostics: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    schedule: Mapped[Schedule | None] = relationship(back_populates="runs")

    __table_args__ = (CheckConstraint("engine IN ('fcfs','cpsat')"),)


class OperationalEvent(Base):
    """Real-world event that can trigger replanning."""

    __tablename__ = "operational_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('machine_breakdown','material_delay','urgent_order','order_cancelled',"
            "'operator_absence','unplanned_maintenance','quantity_change','demand_surge')"
        ),
    )


class AuditLog(Base):
    """Immutable audit trail."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False, index=True
    )


class Job(Base):
    """Background job (long running optimisation) with progress + cancellation."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False, index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    message: Mapped[str | None] = mapped_column(String(240))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled','expired')"
        ),
    )


class User(Base):
    """Platform user with a role based access level."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(160))
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="viewer", nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (CheckConstraint("role IN ('admin','planner','viewer')"),)
