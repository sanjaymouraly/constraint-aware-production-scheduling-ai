"""Catalog entities: customers, products, routings, materials."""

from __future__ import annotations

from datetime import datetime

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


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    priority_tier: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    work_orders: Mapped[list[WorkOrder]] = relationship(back_populates="customer")

    __table_args__ = (CheckConstraint("priority_tier >= 1 AND priority_tier <= 5"),)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    #: Setup family — used to look up sequence-dependent setup times.
    family: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    unit: Mapped[str] = mapped_column(String(24), default="pcs")
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    routings: Mapped[list[Routing]] = relationship(back_populates="product")

    __table_args__ = (CheckConstraint("unit_cost >= 0"),)


class Routing(Base):
    __tablename__ = "routings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(16), default="1")
    notes: Mapped[str | None] = mapped_column(Text)

    product: Mapped[Product] = relationship(back_populates="routings")
    operations: Mapped[list[RoutingOperation]] = relationship(
        back_populates="routing",
        order_by="RoutingOperation.sequence",
        cascade="all, delete-orphan",
    )


class RoutingOperation(Base):
    """One step of a routing (an operation *type*)."""

    __tablename__ = "routing_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    routing_id: Mapped[int] = mapped_column(
        ForeignKey("routings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    machine_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    #: Per-unit processing minutes (multiplied by work-order quantity).
    duration_per_unit_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Fixed (non quantity-proportional) minutes, e.g. programming or first-article check.
    fixed_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Optional maximum units per process batch. None means a single lot.
    batch_size: Mapped[int | None] = mapped_column(Integer)
    #: Internal machine minutes to change over between batches of this operation.
    batch_setup_minutes: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0", nullable=False
    )
    minimum_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Transfer time to the next operation (minutes).
    transfer_time_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Skills an operator must hold to run this operation.
    required_skills: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    #: Machine ids explicitly preferred/allowed; empty means "any machine of the type".
    preferred_machine_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    allow_alternative_machines: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_split: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    crew_size: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_parallel_group: Mapped[str | None] = mapped_column(String(32))
    setup_family: Mapped[str | None] = mapped_column(String(48))
    hourly_cost: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str | None] = mapped_column(Text)

    routing: Mapped[Routing] = relationship(back_populates="operations")
    material_requirements: Mapped[list[MaterialRequirement]] = relationship(
        back_populates="operation", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("routing_id", "sequence", name="uq_routing_operation_seq"),
        CheckConstraint("sequence >= 0"),
        CheckConstraint("duration_per_unit_minutes >= 0"),
        CheckConstraint(
            "batch_size IS NULL OR batch_size >= 1", name="ck_routing_operation_batch_size"
        ),
        CheckConstraint(
            "batch_setup_minutes >= 0", name="ck_routing_operation_batch_setup_minutes"
        ),
        CheckConstraint("crew_size >= 1"),
    )


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(24), default="kg")
    on_hand: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    safety_stock: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cost_per_unit: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    receipts: Mapped[list[MaterialReceipt]] = relationship(
        back_populates="material", cascade="all, delete-orphan"
    )
    requirements: Mapped[list[MaterialRequirement]] = relationship(back_populates="material")

    __table_args__ = (
        CheckConstraint("on_hand >= 0"),
        CheckConstraint("safety_stock >= 0"),
    )


class MaterialReceipt(Base):
    """Time-phased inbound delivery of material."""

    __tablename__ = "material_receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expected_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(64))
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    material: Mapped[Material] = relationship(back_populates="receipts")

    __table_args__ = (CheckConstraint("quantity > 0"),)


class MaterialRequirement(Base):
    """Consumption of a material by a routing operation."""

    __tablename__ = "material_requirements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("routing_operations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity_per_unit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    scrap_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    operation: Mapped[RoutingOperation] = relationship(back_populates="material_requirements")
    material: Mapped[Material] = relationship(back_populates="requirements")

    __table_args__ = (
        UniqueConstraint("operation_id", "material_id", name="uq_operation_material"),
        CheckConstraint("quantity_per_unit >= 0"),
    )


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    routing_id: Mapped[int] = mapped_column(
        ForeignKey("routings.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=1)
    #: 1 = lowest, 5 = highest.
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False, index=True)
    release_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    #: Mandatory delivery commitment — a hard constraint that may never be violated.
    hard_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(24), default="released", nullable=False, index=True)
    penalty_per_hour: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_urgent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    customer: Mapped[Customer] = relationship(back_populates="work_orders")
    product: Mapped[Product] = relationship()
    routing: Mapped[Routing] = relationship()

    __table_args__ = (
        CheckConstraint("quantity > 0"),
        CheckConstraint("priority >= 1 AND priority <= 5"),
        CheckConstraint("penalty_per_hour >= 0"),
        Index("ix_work_orders_due_status", "due_at", "status"),
    )
