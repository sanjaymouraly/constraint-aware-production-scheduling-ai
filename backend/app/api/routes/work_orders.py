"""Work order management endpoints including CSV import/export."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession, Pagination
from app.models import Customer, Product, Routing, User, WorkOrder
from app.schemas.api import (
    WorkOrderIn,
    WorkOrderOut,
    WorkOrderUpdate,
)
from app.services import audit
from app.services.auth import require_planner

router = APIRouter(prefix="/work-orders", tags=["work-orders"])

CSV_COLUMNS = [
    "code",
    "customer_code",
    "product_sku",
    "quantity",
    "priority",
    "release_at",
    "due_at",
    "hard_deadline",
    "penalty_per_hour",
    "is_urgent",
    "status",
    "notes",
]


def _enrich(row: WorkOrder) -> WorkOrderOut:
    item = WorkOrderOut.model_validate(row)
    item.customer_name = row.customer.name if row.customer else None
    item.product_sku = row.product.sku if row.product else None
    return item


@router.get("", response_model=list[WorkOrderOut])
def list_work_orders(
    db: DbSession,
    _: CurrentUser,
    page: Pagination = Depends(),
    status_filter: str | None = Query(default=None, alias="status"),
    customer_id: int | None = None,
    product_id: int | None = None,
    priority: int | None = Query(default=None, ge=1, le=5),
    search: str | None = Query(default=None, max_length=64),
    only_urgent: bool = False,
) -> list[WorkOrderOut]:
    stmt = select(WorkOrder).order_by(WorkOrder.due_at, WorkOrder.code)
    if status_filter:
        stmt = stmt.where(WorkOrder.status == status_filter)
    if customer_id:
        stmt = stmt.where(WorkOrder.customer_id == customer_id)
    if product_id:
        stmt = stmt.where(WorkOrder.product_id == product_id)
    if priority:
        stmt = stmt.where(WorkOrder.priority == priority)
    if only_urgent:
        stmt = stmt.where(WorkOrder.is_urgent.is_(True))
    if search:
        stmt = stmt.where(WorkOrder.code.ilike(f"%{search}%"))
    rows = db.scalars(stmt.offset(page.offset).limit(page.limit)).all()
    return [_enrich(row) for row in rows]


@router.get("/template.csv")
def download_template(_: CurrentUser) -> StreamingResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    writer.writerow(
        [
            "WO-EXAMPLE-1",
            "CUST-NORTHWIND",
            "PMP-100",
            "40",
            "3",
            datetime.now(UTC).isoformat(),
            (datetime.now(UTC)).isoformat(),
            "",
            "75",
            "false",
            "released",
            "Example row",
        ]
    )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=work_orders_template.csv"},
    )


@router.get("/export.csv")
def export_work_orders(db: DbSession, _: CurrentUser) -> StreamingResponse:
    rows = db.scalars(select(WorkOrder).order_by(WorkOrder.code)).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow(
            [
                row.code,
                row.customer.code if row.customer else "",
                row.product.sku if row.product else "",
                row.quantity,
                row.priority,
                row.release_at.isoformat() if row.release_at else "",
                row.due_at.isoformat() if row.due_at else "",
                row.hard_deadline.isoformat() if row.hard_deadline else "",
                row.penalty_per_hour,
                str(row.is_urgent).lower(),
                row.status,
                row.notes or "",
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=work_orders.csv"},
    )


@router.post("/import", response_model=dict)
async def import_work_orders(
    db: DbSession,
    user: User = Depends(require_planner),
    file: UploadFile = File(...),
    dry_run: bool = Query(default=False),
) -> dict[str, Any]:
    """Import work orders from a CSV file with row level validation."""
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The uploaded file is too large (max 5 MB).")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="The file must be UTF-8 encoded.") from exc

    is_json = (file.filename or "").lower().endswith(".json") or "json" in (
        file.content_type or ""
    ).lower()
    rows: list[dict[str, Any]]
    first_row_number: int
    if is_json:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc.msg}.") from exc
        if isinstance(payload, dict):
            payload = payload.get("work_orders", payload.get("items"))
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise HTTPException(
                status_code=400,
                detail="JSON must be an array of work-order objects or an object with a 'work_orders' array.",
            )
        rows = payload
        first_row_number = 1
    else:
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise HTTPException(status_code=400, detail="The CSV file is empty.")
        missing = [
            c for c in ("customer_code", "product_sku", "quantity") if c not in reader.fieldnames
        ]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"The CSV file is missing required columns: {', '.join(missing)}.",
            )
        rows = [dict(row) for row in reader]
        first_row_number = 2

    errors: list[dict[str, Any]] = []
    prepared: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=first_row_number):
        try:
            prepared.append(_prepare_row(db, row, index))
        except ValueError as exc:
            errors.append({"row": index, "message": str(exc), "data": dict(row)})

    created = 0
    if not dry_run and not errors:
        for item in prepared:
            work_order = WorkOrder(
                code=item["code"],
                customer_id=item["customer_id"],
                product_id=item["product_id"],
                routing_id=item["routing_id"],
                quantity=item["quantity"],
                priority=item["priority"],
                release_at=item["release_at"],
                due_at=item["due_at"],
                hard_deadline=item["hard_deadline"],
                status=item["status"],
                penalty_per_hour=item["penalty_per_hour"],
                is_urgent=item["is_urgent"],
                notes=item["notes"],
            )
            db.add(work_order)
            created += 1
        db.flush()
        audit.record(
            db,
            actor=user.username,
            action="work_orders.import",
            entity_type="work_order",
            details={"created": created},
        )
    return {
        "rows": len(prepared) + len(errors),
        "valid": len(prepared),
        "errors": errors,
        "created": created,
        "dry_run": dry_run,
    }


@router.get("/{work_order_id}", response_model=WorkOrderOut)
def get_work_order(work_order_id: int, db: DbSession, _: CurrentUser) -> WorkOrderOut:
    row = db.get(WorkOrder, work_order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Work order not found.")
    return _enrich(row)


@router.post("", response_model=WorkOrderOut, status_code=status.HTTP_201_CREATED)
def create_work_order(
    payload: WorkOrderIn, db: DbSession, user: User = Depends(require_planner)
) -> WorkOrderOut:
    product = db.get(Product, payload.product_id)
    if product is None:
        raise HTTPException(status_code=400, detail="Unknown product.")
    routing = (
        db.get(Routing, payload.routing_id)
        if payload.routing_id
        else db.scalars(select(Routing).where(Routing.product_id == product.id)).first()
    )
    if routing is None:
        raise HTTPException(status_code=400, detail="The product has no routing.")
    code = payload.code or f"WO-{uuid.uuid4().hex[:10].upper()}"
    if db.scalars(select(WorkOrder).where(WorkOrder.code == code)).first():
        raise HTTPException(status_code=409, detail=f"Work order '{code}' already exists.")
    data = payload.model_dump()
    data.pop("code", None)
    data.pop("routing_id", None)
    work_order = WorkOrder(code=code, routing_id=routing.id, **data)
    db.add(work_order)
    db.flush()
    audit.record(
        db,
        actor=user.username,
        action="work_order.create",
        entity_type="work_order",
        entity_id=work_order.id,
    )
    return _enrich(work_order)


@router.patch("/{work_order_id}", response_model=WorkOrderOut)
def update_work_order(
    work_order_id: int,
    payload: WorkOrderUpdate,
    db: DbSession,
    user: User = Depends(require_planner),
) -> WorkOrderOut:
    row = db.get(WorkOrder, work_order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Work order not found.")
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(row, key, value)
    db.flush()
    audit.record(
        db,
        actor=user.username,
        action="work_order.update",
        entity_type="work_order",
        entity_id=row.id,
        details=payload.model_dump(exclude_none=True, mode="json"),
    )
    return _enrich(row)


@router.delete(
    "/{work_order_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
def delete_work_order(
    work_order_id: int, db: DbSession, user: User = Depends(require_planner)
) -> None:
    row = db.get(WorkOrder, work_order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Work order not found.")
    db.delete(row)
    audit.record(
        db,
        actor=user.username,
        action="work_order.delete",
        entity_type="work_order",
        entity_id=work_order_id,
    )


def _prepare_row(db: Session, row: dict[str, Any], index: int) -> dict[str, Any]:
    def value(name: str, default: str = "") -> str:
        raw = row.get(name)
        if raw is None or raw == "":
            return default
        return str(raw).strip()

    customer = db.scalars(select(Customer).where(Customer.code == value("customer_code"))).first()
    if customer is None:
        raise ValueError(f"Unknown customer '{value('customer_code')}'.")
    product = db.scalars(select(Product).where(Product.sku == value("product_sku"))).first()
    if product is None:
        raise ValueError(f"Unknown product '{value('product_sku')}'.")
    routing = db.scalars(select(Routing).where(Routing.product_id == product.id)).first()
    if routing is None:
        raise ValueError(f"Product '{product.sku}' has no routing.")
    try:
        quantity = float(value("quantity"))
    except ValueError as exc:
        raise ValueError(f"'{value('quantity')}' is not a valid quantity.") from exc
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")
    try:
        priority = int(value("priority", "3"))
    except ValueError as exc:
        raise ValueError("Priority must be an integer between 1 and 5.") from exc
    if priority < 1 or priority > 5:
        raise ValueError("Priority must be between 1 and 5.")
    release = _parse_dt(value("release_at"))
    due = _parse_dt(value("due_at"))
    if release is None:
        raise ValueError("release_at is required (ISO 8601).")
    if due is None:
        raise ValueError("due_at is required (ISO 8601).")
    if due <= release:
        raise ValueError("due_at must be after release_at.")
    hard = _parse_dt(value("hard_deadline"))
    code = value("code") or f"WO-{uuid.uuid4().hex[:10].upper()}"
    if db.scalars(select(WorkOrder).where(WorkOrder.code == code)).first():
        raise ValueError(f"Work order '{code}' already exists.")
    try:
        penalty = float(value("penalty_per_hour", "0") or 0)
    except ValueError:
        raise ValueError("penalty_per_hour must be a number.") from None
    return {
        "code": code,
        "customer_id": customer.id,
        "product_id": product.id,
        "routing_id": routing.id,
        "quantity": quantity,
        "priority": priority,
        "release_at": release,
        "due_at": due,
        "hard_deadline": hard,
        "status": value("status", "released") or "released",
        "penalty_per_hour": penalty,
        "is_urgent": value("is_urgent", "false").lower() in {"1", "true", "yes"},
        "notes": value("notes") or None,
    }


def _parse_dt(text: str) -> datetime | None:
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
