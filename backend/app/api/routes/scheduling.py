"""Scheduling, schedule inspection, comparison and export endpoints."""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models import (
    OptimizationRun,
    Schedule,
    ScheduleOperation,
    User,
)
from app.schemas.api import (
    RunRequestModel,
    ScheduleDetail,
    ScheduleOperationOut,
    ScheduleOut,
)
from app.services import audit
from app.services.auth import require_planner
from app.services.scheduling_service import (
    RunRequest,
    SchedulingError,
    TooManyOptimizations,
    compare_schedules,
    load_assignments,
    publish_schedule,
    run_schedule,
)

router = APIRouter(tags=["scheduling"])


@router.get("/scheduling/objectives")
def list_objectives(_: CurrentUser) -> dict[str, Any]:
    from app.scheduling.cpsat import DEFAULT_WEIGHTS, OBJECTIVE_PRESETS

    return {
        "presets": OBJECTIVE_PRESETS,
        "default_weights": DEFAULT_WEIGHTS,
        "terms": ["tardiness", "makespan", "setup", "overtime", "stability", "flow"],
    }


@router.post("/scheduling/run")
def run(
    payload: RunRequestModel, db: DbSession, user: User = Depends(require_planner)
) -> dict[str, Any]:
    """Execute a scheduling run and persist the resulting plan version."""
    request = RunRequest(
        engine=payload.engine,
        horizon_start=payload.horizon_start,
        horizon_end=payload.horizon_end,
        work_order_ids=payload.work_order_ids,
        customer_ids=payload.customer_ids,
        machine_ids=payload.machine_ids,
        time_limit_seconds=payload.time_limit_seconds,
        workers=payload.workers,
        random_seed=payload.random_seed,
        objective_name=payload.objective_name,
        weights=payload.weights,
        objective_mode=payload.objective_mode,
        name=payload.name,
        notes=payload.notes,
        publish=payload.publish,
        base_schedule_id=payload.base_schedule_id,
        stability_schedule_id=payload.stability_schedule_id,
        scenario_id=payload.scenario_id,
        actor=user.username,
    )
    try:
        outcome = run_schedule(db, request)
    except TooManyOptimizations as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except SchedulingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.publish and outcome.schedule is not None:
        try:
            publish_schedule(db, outcome.schedule.id, user.username)
        except SchedulingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "status": outcome.result.status,
        "solver_status": outcome.result.status,
        "objective_value": outcome.result.objective_value,
        "best_bound": outcome.result.best_bound,
        "runtime_ms": round(outcome.result.runtime_ms, 1),
        "metrics": outcome.metrics,
        "validation": outcome.validation.to_dict(),
        "diagnostics": [d.to_dict() for d in outcome.result.diagnostics],
        "unscheduled": outcome.result.unscheduled,
        "schedule": ScheduleOut.model_validate(outcome.schedule).model_dump(mode="json")
        if outcome.schedule
        else None,
        "baseline_metrics": outcome.baseline_metrics,
        "comparison": outcome.comparison,
        "parameters": outcome.result.parameters,
    }


@router.get("/schedules", response_model=list[ScheduleOut])
def list_schedules(
    db: DbSession,
    _: CurrentUser,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    only_published: bool = False,
) -> list[ScheduleOut]:
    stmt = select(Schedule).order_by(Schedule.created_at.desc())
    if only_published:
        stmt = stmt.where(Schedule.is_published.is_(True))
    rows = db.scalars(stmt.offset(offset).limit(limit)).all()
    return [ScheduleOut.model_validate(row) for row in rows]


@router.get("/schedules/{schedule_id}", response_model=ScheduleDetail)
def get_schedule(schedule_id: int, db: DbSession, _: CurrentUser) -> ScheduleDetail:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    detail = ScheduleDetail.model_validate(schedule)
    detail.operations = [
        ScheduleOperationOut.model_validate(_operation_payload(row)) for row in schedule.operations
    ]
    return detail


@router.get("/schedules/{schedule_id}/gantt")
def gantt(schedule_id: int, db: DbSession, _: CurrentUser) -> dict[str, Any]:
    """Gantt payload grouped by machine."""
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    rows = []
    for op in schedule.operations:
        payload = _operation_payload(op)
        rows.append(payload)
    machines: dict[int, dict[str, Any]] = {}
    for row in rows:
        entry = machines.setdefault(
            row["machine_id"],
            {
                "machine_id": row["machine_id"],
                "machine_code": row["machine_code"],
                "operations": [],
            },
        )
        entry["operations"].append(row)
    return {
        "schedule_id": schedule.id,
        "horizon_start": schedule.horizon_start,
        "horizon_end": schedule.horizon_end,
        "machines": list(machines.values()),
        "operations": rows,
    }


@router.post("/schedules/{schedule_id}/publish", response_model=ScheduleOut)
def publish(schedule_id: int, db: DbSession, user: User = Depends(require_planner)) -> ScheduleOut:
    try:
        schedule = publish_schedule(db, schedule_id, user.username)
    except SchedulingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ScheduleOut.model_validate(schedule)


@router.delete(
    "/schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
def delete_schedule(schedule_id: int, db: DbSession, user: User = Depends(require_planner)) -> None:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    if schedule.is_published:
        raise HTTPException(
            status_code=409, detail="A published schedule cannot be deleted; archive it instead."
        )
    db.delete(schedule)
    audit.record(
        db,
        actor=user.username,
        action="schedule.delete",
        entity_type="schedule",
        entity_id=schedule_id,
    )


@router.get("/schedules/{schedule_id}/export.csv")
def export_schedule(schedule_id: int, db: DbSession, _: CurrentUser) -> StreamingResponse:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "work_order_code",
            "operation_sequence",
            "operation_name",
            "machine_code",
            "operator_code",
            "start",
            "end",
            "setup_minutes",
            "processing_minutes",
            "locked",
        ]
    )
    for op in schedule.operations:
        payload = _operation_payload(op)
        writer.writerow(
            [
                payload["work_order_code"],
                payload["operation_sequence"],
                payload["operation_name"],
                payload["machine_code"],
                payload["operator_code"],
                payload["starts_at"],
                payload["ends_at"],
                payload["setup_minutes"],
                payload["processing_minutes"],
                payload["is_locked"],
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=schedule_{schedule.code}.csv"},
    )


@router.get("/schedules/{schedule_id}/runs")
def schedule_runs(schedule_id: int, db: DbSession, _: CurrentUser) -> list[dict]:
    rows = db.scalars(
        select(OptimizationRun).where(OptimizationRun.schedule_id == schedule_id)
    ).all()
    return [
        {
            "id": row.id,
            "engine": row.engine,
            "status": row.status,
            "solver_status": row.solver_status,
            "objective_name": row.objective_name,
            "objective_value": row.objective_value,
            "best_bound": row.best_bound,
            "runtime_ms": row.runtime_ms,
            "created_at": row.created_at,
            "created_by": row.created_by,
            "parameters": row.parameters,
            "diagnostics": row.diagnostics,
        }
        for row in rows
    ]


@router.post("/schedules/compare")
def compare(payload: dict[str, list[int]], db: DbSession, _: CurrentUser) -> dict[str, Any]:
    schedule_ids = payload.get("schedule_ids") or []
    try:
        return compare_schedules(db, schedule_ids)
    except SchedulingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/schedules/{schedule_id}/assignments")
def schedule_assignments(schedule_id: int, db: DbSession, _: CurrentUser) -> dict[str, Any]:
    assignments = load_assignments(db, schedule_id)
    return {
        "schedule_id": schedule_id,
        "assignments": [
            {
                "task_key": a.task_key,
                "work_order_code": a.work_order_code,
                "operation_id": a.operation_id,
                "sequence": a.sequence,
                "name": a.name,
                "machine_id": a.machine_id,
                "machine_code": a.machine_code,
                "operator_id": a.operator_id,
                "operator_code": a.operator_code,
                "start": a.start,
                "end": a.end,
                "processing_minutes": a.processing_minutes,
                "setup_minutes": a.setup_minutes,
                "is_locked": a.is_locked,
            }
            for a in assignments
        ],
    }


def _operation_payload(row: ScheduleOperation) -> dict[str, Any]:
    work_order = row.work_order
    operation = row.routing_operation
    machine = row.machine
    operator = row.operator
    return {
        "id": row.id,
        "schedule_id": row.schedule_id,
        "work_order_id": row.work_order_id,
        "work_order_code": work_order.code if work_order else None,
        "routing_operation_id": row.routing_operation_id,
        "operation_name": operation.name if operation else None,
        "operation_sequence": operation.sequence if operation else None,
        "machine_id": row.machine_id or 0,
        "machine_code": machine.code if machine else None,
        "operator_id": row.operator_id,
        "operator_code": operator.code if operator else None,
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "setup_starts_at": row.setup_starts_at,
        "setup_minutes": row.setup_minutes,
        "processing_minutes": row.processing_minutes,
        "is_locked": row.is_locked,
        "status": row.status,
    }
