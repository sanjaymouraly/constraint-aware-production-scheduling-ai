"""System endpoints: health, jobs, events, audit, dashboard metrics, benchmarks."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm.exc import StaleDataError

from app.api.deps import CurrentUser, DbSession
from app.config import settings
from app.logging_config import log
from app.models import (
    AuditLog,
    Job,
    Machine,
    Material,
    OperationalEvent,
    Operator,
    Schedule,
    User,
    WorkOrder,
)
from app.schemas.api import RunRequestModel
from app.services.auth import require_planner
from app.services.scheduling_service import RunRequest, SchedulingError, run_schedule

router = APIRouter(tags=["system"])

#: In-process worker registry.  A production deployment can move this to a
#: dedicated worker process; the API contract stays the same.
_workers: dict[str, threading.Thread] = {}
_cancelled: set[str] = set()


@router.get("/health")
def health() -> dict[str, Any]:
    """Liveness probe — always cheap, never touches the database."""
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


@router.get("/ready")
def ready(db: DbSession) -> dict[str, Any]:
    """Readiness probe — verifies that the database answers."""
    started = time.perf_counter()
    try:
        db.execute(select(func.count()).select_from(WorkOrder))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database not ready: {exc}") from exc
    return {
        "status": "ready",
        "database": "ok",
        "database_latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "ai_enabled": settings.ai_enabled,
    }


@router.get("/dashboard")
def dashboard(db: DbSession, _: CurrentUser) -> dict[str, Any]:
    """Aggregated KPI payload for the overview dashboard."""
    work_orders = db.execute(
        select(
            WorkOrder.status,
            func.count(WorkOrder.id),
            func.coalesce(func.sum(WorkOrder.quantity), 0),
        ).group_by(WorkOrder.status)
    ).all()
    published = db.scalars(
        select(Schedule)
        .where(Schedule.is_published.is_(True))
        .order_by(Schedule.published_at.desc())
        .limit(1)
    ).first()
    latest = db.scalars(select(Schedule).order_by(Schedule.created_at.desc()).limit(1)).first()
    active_schedule = published or latest
    metrics = (active_schedule.metrics if active_schedule else {}) or {}

    machines = db.execute(
        select(Machine.id, Machine.code, Machine.machine_type).where(Machine.is_active.is_(True))
    ).all()
    operators = db.execute(
        select(Operator.id, Operator.code).where(Operator.is_active.is_(True))
    ).all()
    materials = db.execute(
        select(Material.sku, Material.on_hand, Material.safety_stock).where(
            Material.is_active.is_(True)
        )
    ).all()
    low_stock = [
        {"sku": sku, "on_hand": on_hand, "safety_stock": safety}
        for sku, on_hand, safety in materials
        if safety and on_hand < safety
    ]
    events = db.scalars(
        select(OperationalEvent).order_by(OperationalEvent.created_at.desc()).limit(8)
    ).all()

    return {
        "work_orders": {
            "by_status": {status_: count for status_, count, _ in work_orders},
            "total": sum(count for _, count, _ in work_orders),
            "total_quantity": float(sum(quantity for _, _, quantity in work_orders)),
            "open": sum(
                count
                for status_, count, _ in work_orders
                if status_ not in {"completed", "cancelled"}
            ),
        },
        "resources": {
            "machines": len(machines),
            "operators": len(operators),
            "materials": len(materials),
            "low_stock_materials": low_stock,
        },
        "active_schedule": (
            {
                "id": active_schedule.id,
                "code": active_schedule.code,
                "name": active_schedule.name,
                "source": active_schedule.source,
                "status": active_schedule.status,
                "created_at": active_schedule.created_at,
                "metrics": metrics,
                "validation": active_schedule.validation,
            }
            if active_schedule
            else None
        ),
        "recent_events": [
            {
                "id": event.id,
                "kind": event.kind,
                "severity": event.severity,
                "title": event.title,
                "created_at": event.created_at,
            }
            for event in events
        ],
    }


@router.get("/events", response_model=list[dict])
def list_events(
    db: DbSession,
    _: CurrentUser,
    limit: int = Query(default=50, ge=1, le=200),
    kind: str | None = None,
) -> list[dict]:
    stmt = select(OperationalEvent).order_by(OperationalEvent.created_at.desc())
    if kind:
        stmt = stmt.where(OperationalEvent.kind == kind)
    rows = db.scalars(stmt.limit(limit)).all()
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "severity": row.severity,
            "title": row.title,
            "description": row.description,
            "payload": row.payload,
            "created_at": row.created_at,
            "created_by": row.created_by,
        }
        for row in rows
    ]


@router.get("/audit", response_model=list[dict])
def list_audit(
    db: DbSession, _: CurrentUser, limit: int = Query(default=100, ge=1, le=500)
) -> list[dict]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return [
        {
            "id": row.id,
            "actor": row.actor,
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "details": row.details,
            "created_at": row.created_at,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Background jobs
# ---------------------------------------------------------------------------


def _mark_failed(session, job_id: int, error: str, message: str) -> None:
    """Mark a job as failed; tolerates a job row that no longer exists."""
    try:
        session.rollback()
        current = session.get(Job, job_id)
        if current is None:
            return
        current.status = "failed"
        current.error = error
        current.message = message
        current.finished_at = datetime.now(UTC)
        session.commit()
    except Exception:  # pragma: no cover - defensive
        session.rollback()


def _execute_job(job_id: int, payload: RunRequestModel, actor: str) -> None:
    """Run a queued scheduling job in its own database session."""
    from app.db import SessionLocal

    with SessionLocal() as worker_db:
        worker_job = worker_db.get(Job, job_id)
        if worker_job is None:  # pragma: no cover - defensive
            return
        worker_job.status = "running"
        worker_job.started_at = datetime.now(UTC)
        worker_job.progress = 0.1
        worker_job.message = "Building the scheduling instance"
        worker_db.commit()

        def _cancelled() -> bool:
            worker_db.expire_all()
            current = worker_db.get(Job, job_id)
            if current is None:
                return True
            worker_db.commit()
            return bool(current.cancel_requested)

        try:
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
                actor=actor,
            )
            outcome = run_schedule(
                worker_db,
                request,
                cancel_check=_cancelled,
                guard_wait_seconds=600.0,
            )
            worker_db.expire_all()
            current = worker_db.get(Job, job_id)
            if current is None:  # pragma: no cover - defensive
                return
            if current.cancel_requested:
                current.status = "cancelled"
                current.message = "Cancelled by the operator"
                current.finished_at = datetime.now(UTC)
            else:
                current.status = "succeeded"
                current.progress = 1.0
                current.finished_at = datetime.now(UTC)
                current.message = f"Completed with status {outcome.result.status}"
                current.result = {
                    "status": outcome.result.status,
                    "schedule_id": outcome.schedule.id if outcome.schedule else None,
                    "metrics": outcome.metrics,
                    "validation": outcome.validation.to_dict(),
                }
            worker_db.commit()
        except SchedulingError as exc:
            _mark_failed(worker_db, job_id, str(exc), "Scheduling failed")
        except Exception as exc:
            _mark_failed(worker_db, job_id, str(exc), "Unexpected error")
            log.error("job.failed", job_id=job_id, error=str(exc))


@router.post("/jobs", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
def create_job(
    payload: RunRequestModel, db: DbSession, user: User = Depends(require_planner)
) -> dict[str, Any]:
    """Queue a long running optimisation as a background job.

    The job row is written by the request session; the worker thread then
    re-reads the row in its **own** session so that progress updates are
    persisted and never clobber the request transaction.
    """
    job = Job(
        kind=f"schedule.{payload.engine}",
        status="queued",
        progress=0.0,
        message="Queued",
        params=payload.model_dump(mode="json"),
        created_by=user.username,
    )
    db.add(job)
    db.flush()
    job_id = job.id
    params = payload.model_dump(mode="json")
    # The worker reads the job row in its own session, so the row must be
    # committed *before* the thread starts.
    db.commit()
    # The ORM object belongs to the request session which is closed when the
    # response is returned, so every value the worker needs is captured now.
    actor = user.username
    start_event = threading.Event()

    def _worker() -> None:
        if not start_event.wait(timeout=30):  # pragma: no cover - defensive
            return
        try:
            _execute_job(job_id, payload, actor)
        except StaleDataError:  # pragma: no cover - the job row was removed
            log.info("job.vanished", job_id=job_id)
        except Exception as exc:  # pragma: no cover - defensive
            log.error("job.crashed", job_id=job_id, error=str(exc))

    thread = threading.Thread(target=_worker, name=f"job-{job_id}", daemon=True)
    _workers[str(job_id)] = thread
    start_event.set()
    thread.start()
    return {"job_id": job_id, "id": job_id, "status": job.status, "params": params}


@router.get("/jobs", response_model=list[dict])
def list_jobs(
    db: DbSession, _: CurrentUser, limit: int = Query(default=25, ge=1, le=200)
) -> list[dict]:
    rows = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit)).all()
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "status": row.status,
            "progress": row.progress,
            "message": row.message,
            "error": row.error,
            "result": row.result,
            "created_at": row.created_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "created_by": row.created_by,
        }
        for row in rows
    ]


@router.get("/jobs/{job_id}", response_model=dict)
def get_job(job_id: int, db: DbSession, _: CurrentUser) -> dict[str, Any]:
    row = db.get(Job, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "progress": row.progress,
        "message": row.message,
        "error": row.error,
        "result": row.result,
        "params": row.params,
        "created_at": row.created_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
    }


@router.post("/jobs/{job_id}/cancel", response_model=dict)
def cancel_job(job_id: int, db: DbSession, _: CurrentUser) -> dict[str, Any]:
    row = db.get(Job, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if row.status in {"succeeded", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="The job has already finished.")
    row.cancel_requested = True
    row.status = "cancelled"
    row.message = "Cancellation requested"
    row.finished_at = datetime.now(UTC)
    db.flush()
    return {"id": row.id, "status": row.status}
