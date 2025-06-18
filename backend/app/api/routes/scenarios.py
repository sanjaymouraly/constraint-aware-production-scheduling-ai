"""Scenario (what-if) endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models import Scenario, Schedule, User
from app.schemas.api import (
    DisruptionIn,
    RunRequestModel,
    ScenarioCreate,
    ScenarioOut,
)
from app.services import audit
from app.services.auth import require_planner
from app.services.scenarios import (
    Disruption,
    ScenarioError,
    apply_disruption,
    clone_schedule_id,
    create_scenario,
    impact_report,
    scenario_replan_id,
)
from app.services.scheduling_service import (
    RunRequest,
    SchedulingError,
    TooManyOptimizations,
    run_schedule,
)

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@router.get("", response_model=list[ScenarioOut])
def list_scenarios(db: DbSession, _: CurrentUser) -> list[ScenarioOut]:
    rows = db.scalars(select(Scenario).order_by(Scenario.created_at.desc())).all()
    return [ScenarioOut.model_validate(row) for row in rows]


@router.get("/{scenario_id}", response_model=ScenarioOut)
def get_scenario(scenario_id: int, db: DbSession, _: CurrentUser) -> ScenarioOut:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    return ScenarioOut.model_validate(scenario)


@router.post("", response_model=ScenarioOut, status_code=status.HTTP_201_CREATED)
def create(
    payload: ScenarioCreate, db: DbSession, user: User = Depends(require_planner)
) -> ScenarioOut:
    try:
        scenario = create_scenario(
            db,
            base_schedule_id=payload.base_schedule_id,
            name=payload.name,
            description=payload.description,
            actor=user.username,
            lock_started_operations=payload.lock_started_operations,
        )
    except ScenarioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScenarioOut.model_validate(scenario)


@router.post("/{scenario_id}/disruptions", response_model=dict)
def add_disruption(
    scenario_id: int,
    payload: DisruptionIn,
    db: DbSession,
    user: User = Depends(require_planner),
) -> dict[str, Any]:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    disruption = Disruption(
        kind=payload.kind,
        payload=payload.payload,
        title=payload.title,
        description=payload.description,
        severity=payload.severity,
    )
    try:
        event = apply_disruption(db, scenario, disruption, actor=user.username)
    except ScenarioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "event_id": event.id,
        "kind": event.kind,
        "title": event.title,
        "payload": event.payload,
        "created_at": event.created_at,
        "scenario": ScenarioOut.model_validate(scenario).model_dump(mode="json"),
    }


@router.post("/{scenario_id}/simulate")
def simulate(
    scenario_id: int,
    payload: RunRequestModel,
    db: DbSession,
    user: User = Depends(require_planner),
) -> dict[str, Any]:
    """Re-plan the scenario and compare it with the plan it was cloned from."""
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    clone_id = clone_schedule_id(db, scenario)
    request = RunRequest(
        engine=payload.engine,
        horizon_start=payload.horizon_start,
        horizon_end=payload.horizon_end,
        time_limit_seconds=payload.time_limit_seconds,
        workers=payload.workers,
        random_seed=payload.random_seed,
        objective_name=payload.objective_name,
        weights=payload.weights,
        objective_mode=payload.objective_mode,
        name=payload.name or f"Scenario {scenario.code}",
        notes=payload.notes,
        scenario_id=scenario.id,
        parent_schedule_id=scenario.base_schedule_id,
        base_schedule_id=clone_id,
        stability_schedule_id=scenario.base_schedule_id,
        actor=user.username,
    )
    try:
        outcome = run_schedule(db, request)
    except TooManyOptimizations as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except SchedulingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    scenario.status = "simulated"
    report = impact_report(db, scenario, outcome.schedule)
    return {
        "status": outcome.result.status,
        "metrics": outcome.metrics,
        "validation": outcome.validation.to_dict(),
        "comparison": outcome.comparison,
        "impact": report,
        "schedule_id": outcome.schedule.id if outcome.schedule else None,
        "runtime_ms": round(outcome.result.runtime_ms, 1),
    }


@router.get("/{scenario_id}/impact")
def impact(scenario_id: int, db: DbSession, _: CurrentUser) -> dict[str, Any]:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    latest = (
        db.get(Schedule, scenario_replan_id(db, scenario))
        if scenario_replan_id(db, scenario)
        else None
    )
    return impact_report(db, scenario, latest)


@router.post("/{scenario_id}/publish", response_model=dict)
def publish_scenario(
    scenario_id: int, db: DbSession, user: User = Depends(require_planner)
) -> dict[str, Any]:
    """Publish the schedule produced by a scenario as the new live plan."""
    from app.services.scheduling_service import publish_schedule

    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    replan_id = scenario_replan_id(db, scenario)
    latest = db.get(Schedule, replan_id) if replan_id else None
    if latest is None:
        raise HTTPException(status_code=409, detail="The scenario has not been simulated yet.")
    try:
        schedule = publish_schedule(db, latest.id, user.username)
    except SchedulingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    scenario.status = "published"
    audit.record(
        db,
        actor=user.username,
        action="scenario.publish",
        entity_type="scenario",
        entity_id=scenario.id,
        details={"schedule_id": schedule.id},
    )
    return {
        "id": schedule.id,
        "schedule_id": schedule.id,
        "schedule_code": schedule.code,
        "is_published": schedule.is_published,
        "published_at": schedule.published_at,
        "metrics": schedule.metrics,
    }
