"""What-if scenarios and disruption handling.

A scenario is a *clone* of a published plan plus a set of operational
disruptions.  Disruptions are applied to the master data (a machine really
breaks down, a delivery really slips) and recorded as operational events, so
the scenario is fully auditable.  Re-planning never overwrites the live
schedule: a new schedule version is created and must be published explicitly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import planning_now
from app.models import (
    Machine,
    MachineDowntime,
    MaterialReceipt,
    OperationalEvent,
    Operator,
    OperatorAbsence,
    Scenario,
    Schedule,
    ScheduleOperation,
    WorkOrder,
)
from app.services import audit

DISRUPTION_KINDS = (
    "machine_breakdown",
    "material_delay",
    "urgent_order",
    "operator_absence",
    "unplanned_maintenance",
    "order_cancelled",
    "quantity_change",
    "demand_surge",
)


@dataclass
class Disruption:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    title: str = ""
    description: str | None = None
    severity: str = "warning"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "payload": self.payload,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
        }


class ScenarioError(RuntimeError):
    pass


def create_scenario(
    session: Session,
    base_schedule_id: int,
    name: str,
    description: str | None = None,
    actor: str = "system",
    lock_started_operations: bool = True,
) -> Scenario:
    """Clone a schedule into a new scenario with its own draft plan."""
    base = session.get(Schedule, base_schedule_id)
    if base is None:
        raise ScenarioError(f"Schedule {base_schedule_id} does not exist.")

    code = f"SCN-{datetime.now(UTC):%y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
    scenario = Scenario(
        code=code,
        name=name,
        description=description,
        base_schedule_id=base_schedule_id,
        status="draft",
        assumptions={"disruptions": []},
        created_by=actor,
    )
    session.add(scenario)
    session.flush()

    clone = Schedule(
        code=f"{code}-PLAN",
        name=f"{name} (clone of {base.code})",
        version=1,
        status="draft",
        source="imported",
        horizon_start=base.horizon_start,
        horizon_end=base.horizon_end,
        scenario_id=scenario.id,
        parent_schedule_id=base.id,
        is_published=False,
        created_by=actor,
        notes=f"Scenario clone of schedule {base.code}",
        metrics=dict(base.metrics or {}),
        validation=dict(base.validation or {}),
    )
    session.add(clone)
    session.flush()

    now = planning_now()
    for row in base.operations:
        locked = bool(lock_started_operations and row.starts_at <= now)
        session.add(
            ScheduleOperation(
                schedule_id=clone.id,
                work_order_id=row.work_order_id,
                routing_operation_id=row.routing_operation_id,
                machine_id=row.machine_id,
                operator_id=row.operator_id,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
                setup_starts_at=row.setup_starts_at,
                setup_minutes=row.setup_minutes,
                processing_minutes=row.processing_minutes,
                is_locked=locked,
                status="planned",
                sequence_index=row.sequence_index,
            )
        )
    session.flush()

    scenario.assumptions = {
        "disruptions": [],
        "base_schedule_id": base.id,
        "clone_schedule_id": clone.id,
        "locked_operations": sum(1 for r in clone.operations if r.is_locked),
    }
    audit.record(
        session,
        actor=actor,
        action="scenario.create",
        entity_type="scenario",
        entity_id=scenario.id,
        details={"base_schedule_id": base_schedule_id, "clone_schedule_id": clone.id},
    )
    session.flush()
    return scenario


#: Payload keys that each disruption kind requires.
REQUIRED_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "machine_breakdown": ("machine_id",),
    "unplanned_maintenance": ("machine_id",),
    "material_delay": ("receipt_id",),
    "operator_absence": ("operator_id",),
    "urgent_order": ("template_work_order_id",),
    "demand_surge": ("template_work_order_id",),
    "order_cancelled": ("work_order_id",),
    "quantity_change": ("work_order_id",),
}


def _require_payload(payload: dict[str, Any], kind: str) -> None:
    """Fail fast with a user readable message when a payload key is missing."""
    for key in REQUIRED_PAYLOAD_KEYS.get(kind, ()):
        if key not in payload or payload[key] in (None, ""):
            raise ScenarioError(
                f"The '{kind}' disruption requires the '{key}' field in its payload."
            )


def _require_int(payload: dict[str, Any], key: str) -> int:
    try:
        return int(payload[key])
    except (TypeError, ValueError) as exc:
        raise ScenarioError(f"'{key}' must be an integer identifier.") from exc


def apply_disruption(
    session: Session, scenario: Scenario, disruption: Disruption, actor: str = "system"
) -> OperationalEvent:
    """Apply a disruption to the master data and record it as an event."""
    if disruption.kind not in DISRUPTION_KINDS:
        raise ScenarioError(f"Unknown disruption kind '{disruption.kind}'.")

    payload = dict(disruption.payload)
    title = disruption.title or disruption.kind.replace("_", " ").title()
    _require_payload(payload, disruption.kind)

    if disruption.kind in {"machine_breakdown", "unplanned_maintenance"}:
        machine = session.get(Machine, _require_int(payload, "machine_id"))
        if machine is None:
            raise ScenarioError("The disruption references an unknown machine.")
        start = _parse_dt(payload.get("starts_at")) or planning_now()
        hours = float(payload.get("duration_hours", 4))
        end = start + timedelta(hours=hours)
        downtime = MachineDowntime(
            machine_id=machine.id,
            starts_at=start,
            ends_at=end,
            kind="breakdown" if disruption.kind == "machine_breakdown" else "maintenance",
            reason=title,
            is_locked=bool(payload.get("is_locked", False)),
        )
        session.add(downtime)
        session.flush()
        payload["downtime_id"] = downtime.id
        payload["machine_code"] = machine.code

    elif disruption.kind == "material_delay":
        receipt = session.get(MaterialReceipt, _require_int(payload, "receipt_id"))
        if receipt is None:
            raise ScenarioError("The disruption references an unknown material receipt.")
        delay_days = float(payload.get("delay_days", 2))
        original = receipt.expected_at
        receipt.expected_at = original + timedelta(days=delay_days)
        session.flush()
        payload["original_expected_at"] = original.isoformat()
        payload["new_expected_at"] = receipt.expected_at.isoformat()

    elif disruption.kind == "operator_absence":
        operator = session.get(Operator, _require_int(payload, "operator_id"))
        if operator is None:
            raise ScenarioError("The disruption references an unknown operator.")
        start = _parse_dt(payload.get("starts_at")) or planning_now()
        hours = float(payload.get("duration_hours", 8))
        absence = OperatorAbsence(
            operator_id=operator.id,
            starts_at=start,
            ends_at=start + timedelta(hours=hours),
            reason=title,
            is_locked=bool(payload.get("is_locked", False)),
        )
        session.add(absence)
        session.flush()
        payload["absence_id"] = absence.id
        payload["operator_code"] = operator.code

    elif disruption.kind == "urgent_order":
        template = session.get(WorkOrder, _require_int(payload, "template_work_order_id"))
        if template is None:
            raise ScenarioError("The disruption references an unknown work order template.")
        product = template.product
        routing = template.routing
        customer = template.customer
        quantity = float(payload.get("quantity", template.quantity))
        due = _parse_dt(payload.get("due_at")) or (planning_now() + timedelta(days=3))
        release = planning_now() - timedelta(hours=1)
        code = f"{template.code}-URG-{uuid.uuid4().hex[:4].upper()}"
        order = WorkOrder(
            code=code,
            customer_id=customer.id,
            product_id=product.id,
            routing_id=routing.id,
            quantity=quantity,
            priority=5,
            release_at=release,
            due_at=due,
            hard_deadline=_parse_dt(payload.get("hard_deadline")),
            status="released",
            penalty_per_hour=float(payload.get("penalty_per_hour", 250.0)),
            is_urgent=True,
            notes=f"Urgent order created by scenario {scenario.code}",
        )
        session.add(order)
        session.flush()
        payload["work_order_id"] = order.id
        payload["work_order_code"] = order.code

    elif disruption.kind == "order_cancelled":
        target_order = session.get(WorkOrder, _require_int(payload, "work_order_id"))
        if target_order is None:
            raise ScenarioError("The disruption references an unknown work order.")
        payload["previous_status"] = target_order.status
        target_order.status = "cancelled"
        session.flush()

    elif disruption.kind == "quantity_change":
        target_order = session.get(WorkOrder, _require_int(payload, "work_order_id"))
        if target_order is None:
            raise ScenarioError("The disruption references an unknown work order.")
        payload["previous_quantity"] = target_order.quantity
        target_order.quantity = float(payload.get("quantity", target_order.quantity))
        session.flush()

    elif disruption.kind == "demand_surge":
        template = session.get(WorkOrder, _require_int(payload, "template_work_order_id"))
        if template is None:
            raise ScenarioError("The disruption references an unknown work order template.")
        extra = int(payload.get("additional_orders", 2))
        product = template.product
        routing = template.routing
        customer = template.customer
        due = _parse_dt(payload.get("due_at")) or (planning_now() + timedelta(days=5))
        created: list[str] = []
        for index in range(extra):
            code = f"{template.code}-SURGE-{uuid.uuid4().hex[:4].upper()}"
            order = WorkOrder(
                code=code,
                customer_id=customer.id,
                product_id=product.id,
                routing_id=routing.id,
                quantity=float(payload.get("quantity", template.quantity)),
                priority=4,
                release_at=planning_now() - timedelta(hours=1),
                due_at=due,
                status="released",
                penalty_per_hour=float(payload.get("penalty_per_hour", 150.0)),
                notes=f"Demand surge order created by scenario {scenario.code}",
            )
            session.add(order)
            created.append(code)
        session.flush()
        payload["created_work_orders"] = created

    event = OperationalEvent(
        kind=disruption.kind,
        severity=disruption.severity,
        title=title,
        description=disruption.description,
        payload=payload,
        created_by=actor,
    )
    session.add(event)
    session.flush()

    assumptions = dict(scenario.assumptions or {})
    disruptions = list(assumptions.get("disruptions", []))
    entry = disruption.to_dict()
    entry["event_id"] = event.id
    entry["applied_at"] = datetime.now(UTC).isoformat()
    disruptions.append(entry)
    assumptions["disruptions"] = disruptions
    scenario.assumptions = assumptions

    audit.record(
        session,
        actor=actor,
        action=f"scenario.disruption.{disruption.kind}",
        entity_type="scenario",
        entity_id=scenario.id,
        details=payload,
    )
    session.flush()
    return event


def scenario_replan_id(session: Session, scenario: Scenario) -> int | None:
    """The newest *re-planned* schedule of a scenario (never the initial clone)."""
    clone_id = clone_schedule_id(session, scenario)
    stmt = select(Schedule).where(Schedule.scenario_id == scenario.id)
    if clone_id is not None:
        stmt = stmt.where(Schedule.id != clone_id)
    row = session.scalars(stmt.order_by(Schedule.created_at.desc()).limit(1)).first()
    return row.id if row else None


def clone_schedule_id(session: Session, scenario: Scenario) -> int | None:
    assumptions = scenario.assumptions or {}
    clone_id = assumptions.get("clone_schedule_id")
    if clone_id:
        return int(clone_id)
    clone = session.scalars(
        select(Schedule).where(Schedule.scenario_id == scenario.id).limit(1)
    ).first()
    return clone.id if clone else None


def impact_report(
    session: Session, scenario: Scenario, new_schedule: Schedule | None
) -> dict[str, Any]:
    """Compare the scenario plan with the plan it was cloned from."""
    base_schedule = (
        session.get(Schedule, scenario.base_schedule_id) if scenario.base_schedule_id else None
    )
    if base_schedule is None or new_schedule is None:
        return {"available": False}

    base_rows = {r.work_order_id: r for r in base_schedule.operations}
    new_rows = {r.work_order_id: r for r in new_schedule.operations}
    delayed: list[dict[str, Any]] = []
    improved: list[dict[str, Any]] = []
    for work_order_id, row in base_rows.items():
        other = new_rows.get(work_order_id)
        if other is None:
            continue
        delta = (other.ends_at - row.ends_at).total_seconds() / 60.0
        if delta > 1:
            delayed.append({"work_order_id": work_order_id, "delay_minutes": round(delta, 1)})
        elif delta < -1:
            improved.append({"work_order_id": work_order_id, "gain_minutes": round(-delta, 1)})

    base_metrics = base_schedule.metrics or {}
    new_metrics = new_schedule.metrics or {}
    return {
        "available": True,
        "base_schedule_id": base_schedule.id,
        "new_schedule_id": new_schedule.id,
        "delayed_orders": sorted(delayed, key=lambda d: -d["delay_minutes"])[:20],
        "improved_orders": improved[:20],
        "disrupted_operations": sum(
            1
            for row in new_schedule.operations
            if base_rows.get(row.work_order_id)
            and base_rows[row.work_order_id].machine_id != row.machine_id
        ),
        "metrics_before": {
            "on_time_delivery_pct": base_metrics.get("on_time_delivery_pct"),
            "weighted_tardiness": base_metrics.get("weighted_tardiness"),
            "makespan_minutes": base_metrics.get("makespan_minutes"),
        },
        "metrics_after": {
            "on_time_delivery_pct": new_metrics.get("on_time_delivery_pct"),
            "weighted_tardiness": new_metrics.get("weighted_tardiness"),
            "makespan_minutes": new_metrics.get("makespan_minutes"),
        },
    }


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    raise ScenarioError(f"Cannot interpret '{value}' as a timestamp.")
