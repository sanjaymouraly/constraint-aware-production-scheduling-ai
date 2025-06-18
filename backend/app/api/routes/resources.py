"""Resource and catalog endpoints (machines, operators, materials, calendars)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.config import planning_now
from app.models import (
    Calendar,
    CalendarException,
    Customer,
    Machine,
    MachineDowntime,
    MachineSetup,
    Material,
    MaterialReceipt,
    Operator,
    OperatorAbsence,
    Product,
    Routing,
    RoutingOperation,
    User,
)
from app.schemas.api import (
    CalendarExceptionIn,
    CalendarOut,
    CustomerIn,
    CustomerOut,
    MachineDowntimeIn,
    MachineIn,
    MachineOut,
    MachineUpdate,
    MaterialIn,
    MaterialOut,
    MaterialReceiptIn,
    MaterialReceiptOut,
    MaterialUpdate,
    OperatorAbsenceIn,
    OperatorIn,
    OperatorOut,
    OperatorUpdate,
    ProductOut,
    RoutingOperationOut,
    RoutingOperationUpdate,
    RoutingOut,
)
from app.services import audit
from app.services.auth import require_planner

router = APIRouter(tags=["resources"])


# ---------------------------------------------------------------------------
# Customers / products / routings
# ---------------------------------------------------------------------------


@router.get("/customers", response_model=list[CustomerOut])
def list_customers(db: DbSession, _: CurrentUser) -> list[CustomerOut]:
    rows = db.scalars(select(Customer).order_by(Customer.code)).all()
    return [CustomerOut.model_validate(row) for row in rows]


@router.post("/customers", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(
    payload: CustomerIn, db: DbSession, user: User = Depends(require_planner)
) -> CustomerOut:
    if db.scalars(select(Customer).where(Customer.code == payload.code)).first():
        raise HTTPException(status_code=409, detail="Customer code already exists.")
    customer = Customer(**payload.model_dump())
    db.add(customer)
    db.flush()
    return CustomerOut.model_validate(customer)


@router.get("/products", response_model=list[ProductOut])
def list_products(db: DbSession, _: CurrentUser) -> list[ProductOut]:
    rows = db.scalars(select(Product).order_by(Product.sku)).all()
    return [ProductOut.model_validate(row) for row in rows]


@router.get("/routings", response_model=list[RoutingOut])
def list_routings(db: DbSession, _: CurrentUser) -> list[RoutingOut]:
    rows = db.scalars(select(Routing).order_by(Routing.code)).all()
    result: list[RoutingOut] = []
    for row in rows:
        item = RoutingOut.model_validate(row)
        item.operations = [
            RoutingOperationOut.model_validate(op)
            for op in sorted(row.operations, key=lambda o: o.sequence)
        ]
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# Machines
# ---------------------------------------------------------------------------


@router.patch("/routings/operations/{operation_id}", response_model=RoutingOperationOut)
def update_routing_operation(
    operation_id: int,
    payload: RoutingOperationUpdate,
    db: DbSession,
    user: User = Depends(require_planner),
) -> RoutingOperationOut:
    operation = db.get(RoutingOperation, operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Routing operation not found.")
    values = payload.model_dump(exclude_unset=True)
    machine_ids = values.get("preferred_machine_ids")
    if machine_ids is not None:
        found = set(db.scalars(select(Machine.id).where(Machine.id.in_(machine_ids))).all())
        if found != set(machine_ids):
            raise HTTPException(
                status_code=400, detail="One or more preferred machines do not exist."
            )
    for key, value in values.items():
        setattr(operation, key, value)
    db.flush()
    audit.record(
        db,
        actor=user.username,
        action="routing_operation.update",
        entity_type="routing_operation",
        entity_id=operation.id,
        details=values,
    )
    return RoutingOperationOut.model_validate(operation)


@router.get("/machines", response_model=list[MachineOut])
def list_machines(db: DbSession, _: CurrentUser) -> list[MachineOut]:
    rows = db.scalars(select(Machine).order_by(Machine.code)).all()
    result: list[MachineOut] = []
    for row in rows:
        item = MachineOut.model_validate(row)
        item.calendar_code = row.calendar.code if row.calendar else None
        item.downtime_count = len(row.downtimes)
        item.setup_count = len(row.setups)
        result.append(item)
    return result


@router.post("/machines", response_model=MachineOut, status_code=status.HTTP_201_CREATED)
def create_machine(
    payload: MachineIn, db: DbSession, user: User = Depends(require_planner)
) -> MachineOut:
    if db.scalars(select(Machine).where(Machine.code == payload.code)).first():
        raise HTTPException(status_code=409, detail="Machine code already exists.")
    if db.get(Calendar, payload.calendar_id) is None:
        raise HTTPException(status_code=400, detail="The referenced calendar does not exist.")
    machine = Machine(**payload.model_dump())
    db.add(machine)
    db.flush()
    audit.record(
        db,
        actor=user.username,
        action="machine.create",
        entity_type="machine",
        entity_id=machine.id,
    )
    return MachineOut.model_validate(machine)


@router.patch("/machines/{machine_id}", response_model=MachineOut)
def update_machine(
    machine_id: int,
    payload: MachineUpdate,
    db: DbSession,
    user: User = Depends(require_planner),
) -> MachineOut:
    machine = db.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found.")
    values = payload.model_dump(exclude_unset=True)
    calendar_id = values.get("calendar_id")
    if calendar_id is not None and db.get(Calendar, calendar_id) is None:
        raise HTTPException(status_code=400, detail="The referenced calendar does not exist.")
    for key, value in values.items():
        setattr(machine, key, value)
    db.flush()
    audit.record(
        db,
        actor=user.username,
        action="machine.update",
        entity_type="machine",
        entity_id=machine.id,
        details=values,
    )
    result = MachineOut.model_validate(machine)
    result.calendar_code = machine.calendar.code if machine.calendar else None
    result.downtime_count = len(machine.downtimes)
    result.setup_count = len(machine.setups)
    return result


@router.post("/machines/{machine_id}/downtimes", response_model=dict, status_code=201)
def create_machine_downtime(
    machine_id: int,
    payload: MachineDowntimeIn,
    db: DbSession,
    user: User = Depends(require_planner),
) -> dict:
    machine = db.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found.")
    row = MachineDowntime(machine_id=machine_id, **payload.model_dump())
    db.add(row)
    db.flush()
    audit.record(
        db,
        actor=user.username,
        action="machine.downtime.create",
        entity_type="machine_downtime",
        entity_id=row.id,
        details={"machine_id": machine_id, "kind": row.kind},
    )
    return {
        "id": row.id,
        "machine_id": row.machine_id,
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "kind": row.kind,
        "reason": row.reason,
        "is_locked": row.is_locked,
    }


@router.get("/machines/{machine_id}/downtimes")
def list_machine_downtimes(machine_id: int, db: DbSession, _: CurrentUser) -> list[dict]:
    rows = db.scalars(
        select(MachineDowntime)
        .where(MachineDowntime.machine_id == machine_id)
        .order_by(MachineDowntime.starts_at)
    ).all()
    return [
        {
            "id": row.id,
            "machine_id": row.machine_id,
            "starts_at": row.starts_at,
            "ends_at": row.ends_at,
            "kind": row.kind,
            "reason": row.reason,
            "is_locked": row.is_locked,
        }
        for row in rows
    ]


@router.get("/machines/{machine_id}/setups")
def list_machine_setups(machine_id: int, db: DbSession, _: CurrentUser) -> list[dict]:
    rows = db.scalars(select(MachineSetup).where(MachineSetup.machine_id == machine_id)).all()
    return [
        {
            "id": row.id,
            "from_family": row.from_family,
            "to_family": row.to_family,
            "setup_minutes": row.setup_minutes,
            "setup_cost": row.setup_cost,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


@router.get("/operators", response_model=list[OperatorOut])
def list_operators(db: DbSession, _: CurrentUser) -> list[OperatorOut]:
    rows = db.scalars(select(Operator).order_by(Operator.code)).all()
    return [OperatorOut.model_validate(row) for row in rows]


@router.post("/operators", response_model=OperatorOut, status_code=status.HTTP_201_CREATED)
def create_operator(
    payload: OperatorIn, db: DbSession, user: User = Depends(require_planner)
) -> OperatorOut:
    if db.scalars(select(Operator).where(Operator.code == payload.code)).first():
        raise HTTPException(status_code=409, detail="Operator code already exists.")
    if db.get(Calendar, payload.calendar_id) is None:
        raise HTTPException(status_code=400, detail="The referenced calendar does not exist.")
    operator = Operator(**payload.model_dump())
    db.add(operator)
    db.flush()
    audit.record(
        db,
        actor=user.username,
        action="operator.create",
        entity_type="operator",
        entity_id=operator.id,
    )
    return OperatorOut.model_validate(operator)


@router.patch("/operators/{operator_id}", response_model=OperatorOut)
def update_operator(
    operator_id: int,
    payload: OperatorUpdate,
    db: DbSession,
    user: User = Depends(require_planner),
) -> OperatorOut:
    operator = db.get(Operator, operator_id)
    if operator is None:
        raise HTTPException(status_code=404, detail="Operator not found.")
    values = payload.model_dump(exclude_unset=True)
    calendar_id = values.get("calendar_id")
    if calendar_id is not None and db.get(Calendar, calendar_id) is None:
        raise HTTPException(status_code=400, detail="The referenced calendar does not exist.")
    for key, value in values.items():
        setattr(operator, key, value)
    db.flush()
    audit.record(
        db,
        actor=user.username,
        action="operator.update",
        entity_type="operator",
        entity_id=operator.id,
        details=values,
    )
    return OperatorOut.model_validate(operator)


@router.get("/operators/{operator_id}/absences", response_model=list[dict])
def list_operator_absences(operator_id: int, db: DbSession, _: CurrentUser) -> list[dict]:
    if db.get(Operator, operator_id) is None:
        raise HTTPException(status_code=404, detail="Operator not found.")
    rows = db.scalars(
        select(OperatorAbsence)
        .where(OperatorAbsence.operator_id == operator_id)
        .order_by(OperatorAbsence.starts_at)
    ).all()
    return [
        {
            "id": row.id,
            "operator_id": row.operator_id,
            "starts_at": row.starts_at,
            "ends_at": row.ends_at,
            "reason": row.reason,
            "is_locked": row.is_locked,
        }
        for row in rows
    ]


@router.post("/operators/{operator_id}/absences", response_model=dict, status_code=201)
def create_operator_absence(
    operator_id: int,
    payload: OperatorAbsenceIn,
    db: DbSession,
    user: User = Depends(require_planner),
) -> dict:
    operator = db.get(Operator, operator_id)
    if operator is None:
        raise HTTPException(status_code=404, detail="Operator not found.")
    row = OperatorAbsence(operator_id=operator_id, **payload.model_dump())
    db.add(row)
    db.flush()
    audit.record(
        db,
        actor=user.username,
        action="operator.absence.create",
        entity_type="operator_absence",
        entity_id=row.id,
        details={"operator_id": operator_id},
    )
    return {
        "id": row.id,
        "operator_id": row.operator_id,
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "reason": row.reason,
        "is_locked": row.is_locked,
    }


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


@router.get("/materials", response_model=list[MaterialOut])
def list_materials(db: DbSession, _: CurrentUser) -> list[MaterialOut]:
    rows = db.scalars(select(Material).order_by(Material.sku)).all()
    result: list[MaterialOut] = []
    for row in rows:
        item = MaterialOut.model_validate(row)
        item.receipts = [MaterialReceiptOut.model_validate(r) for r in row.receipts]
        item.total_available = row.on_hand + sum(r.quantity for r in row.receipts)
        result.append(item)
    return result


@router.post("/materials", response_model=MaterialOut, status_code=status.HTTP_201_CREATED)
def create_material(
    payload: MaterialIn, db: DbSession, user: User = Depends(require_planner)
) -> MaterialOut:
    if db.scalars(select(Material).where(Material.sku == payload.sku)).first():
        raise HTTPException(status_code=409, detail="Material SKU already exists.")
    material = Material(**payload.model_dump())
    db.add(material)
    db.flush()
    audit.record(
        db,
        actor=user.username,
        action="material.create",
        entity_type="material",
        entity_id=material.id,
    )
    return MaterialOut.model_validate(material)


@router.patch("/materials/{material_id}", response_model=MaterialOut)
def update_material(
    material_id: int,
    payload: MaterialUpdate,
    db: DbSession,
    user: User = Depends(require_planner),
) -> MaterialOut:
    material = db.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found.")
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(material, key, value)
    db.flush()
    audit.record(
        db,
        actor=user.username,
        action="material.update",
        entity_type="material",
        entity_id=material.id,
        details=values,
    )
    result = MaterialOut.model_validate(material)
    result.receipts = [MaterialReceiptOut.model_validate(receipt) for receipt in material.receipts]
    result.total_available = material.on_hand + sum(
        receipt.quantity for receipt in material.receipts
    )
    return result


@router.post("/materials/{material_id}/receipts", response_model=MaterialReceiptOut)
def add_receipt(
    material_id: int,
    payload: MaterialReceiptIn,
    db: DbSession,
    user: User = Depends(require_planner),
) -> MaterialReceiptOut:
    material = db.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found.")
    receipt = MaterialReceipt(
        material_id=material_id,
        expected_at=payload.expected_at,
        quantity=payload.quantity,
        reference=payload.reference,
        is_confirmed=payload.is_confirmed,
    )
    db.add(receipt)
    db.flush()
    audit.record(
        db,
        actor=user.username,
        action="material.receipt.create",
        entity_type="material_receipt",
        entity_id=receipt.id,
        details={"material_id": material_id},
    )
    return MaterialReceiptOut.model_validate(receipt)


# ---------------------------------------------------------------------------
# Calendars
# ---------------------------------------------------------------------------


@router.get("/calendars", response_model=list[CalendarOut])
def list_calendars(db: DbSession, _: CurrentUser) -> list[CalendarOut]:
    rows = db.scalars(select(Calendar).order_by(Calendar.code)).all()
    result: list[CalendarOut] = []
    for row in rows:
        item = CalendarOut.model_validate(row)
        result.append(item)
    return result


@router.post("/calendars/{calendar_id}/exceptions", response_model=CalendarOut, status_code=201)
def create_calendar_exception(
    calendar_id: int,
    payload: CalendarExceptionIn,
    db: DbSession,
    user: User = Depends(require_planner),
) -> CalendarOut:
    calendar = db.get(Calendar, calendar_id)
    if calendar is None:
        raise HTTPException(status_code=404, detail="Calendar not found.")
    exception = CalendarException(calendar_id=calendar_id, **payload.model_dump())
    db.add(exception)
    db.flush()
    audit.record(
        db,
        actor=user.username,
        action="calendar.exception.create",
        entity_type="calendar_exception",
        entity_id=exception.id,
        details={"calendar_id": calendar_id, "kind": exception.kind},
    )
    db.refresh(calendar)
    return CalendarOut.model_validate(calendar)


@router.get("/calendars/{calendar_id}/segments")
def calendar_segments(
    calendar_id: int,
    db: DbSession,
    _: CurrentUser,
    days: int = Query(default=14, ge=1, le=120),
) -> list[dict]:
    """Working segments of a calendar — used by the resource calendar view."""
    from datetime import timedelta

    from app.domain.calendars import CalendarSpec, ExceptionSpec, ShiftSpec, get_engine

    calendar = db.get(Calendar, calendar_id)
    if calendar is None:
        raise HTTPException(status_code=404, detail="Calendar not found.")
    spec = CalendarSpec(
        id=calendar.id,
        code=calendar.code,
        timezone=calendar.timezone,
        is_continuous=calendar.is_continuous,
        shifts=tuple(
            ShiftSpec(s.day_of_week, s.start_minute, s.end_minute, s.label) for s in calendar.shifts
        ),
        breaks=tuple(
            BreakSpecAlias(b.day_of_week, b.start_minute, b.end_minute, b.label)
            for b in calendar.breaks
        ),
        exceptions=tuple(
            ExceptionSpec(e.day, e.kind, e.start_minute, e.end_minute, e.label)
            for e in calendar.exceptions
        ),
    )
    start = planning_now()
    end = start + timedelta(days=days)
    availability = get_engine().availability(
        spec, start, end, resource_id=calendar.id, resource_kind="calendar"
    )
    return [
        {"start": segment.start, "end": segment.end, "minutes": segment.minutes}
        for segment in availability.working
    ]


def BreakSpecAlias(day_of_week: int, start_minute: int, end_minute: int, label: str | None):
    from app.domain.calendars import BreakSpec

    return BreakSpec(day_of_week, start_minute, end_minute, label)
