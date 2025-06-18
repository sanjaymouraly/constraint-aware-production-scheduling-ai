"""Synthetic but realistic demonstration factory.

The data set is fully deterministic (seeded) so that every documented
benchmark result can be reproduced.  Three scales are provided:

``small``    – 6 work orders, ideal for exact correctness tests
``medium``   – 20 work orders, the interactive demo factory
``large``    – 120 work orders, used for performance benchmarks

Assumptions (documented in ``docs/ARCHITECTURE.md``):

* Plant calendar ``PLANT-STD``: Monday–Friday, two 8-hour shifts
  (06:00–14:00 and 14:00–22:00 plant local time), no breaks.
* Calendar ``PLANT-24X7``: continuous availability, used by the furnace whose
  operations legitimately run across shifts.
* Machine setup matrices satisfy the triangle inequality, which makes the
  pairwise setup model equivalent to the exact chain model.
* One week of incoming material receipts is scheduled so that time-phased
  inventory really constrains the plan.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import planning_now
from app.models import (
    AIConversation,
    AIMessage,
    AIProvider,
    AISettings,
    AuditLog,
    Calendar,
    CalendarBreak,
    CalendarException,
    CalendarShift,
    Customer,
    Job,
    Machine,
    MachineCalendarException,
    MachineDowntime,
    MachineSetup,
    Material,
    MaterialReceipt,
    MaterialRequirement,
    OperationalEvent,
    Operator,
    OperatorAbsence,
    OptimizationRun,
    Product,
    Routing,
    RoutingOperation,
    Scenario,
    Schedule,
    ScheduleOperation,
    WorkOrder,
)

FAMILIES = ("A", "B", "C")


@dataclass(frozen=True)
class ScaleProfile:
    """Shape of a demo dataset.

    ``orders``       number of work orders in the plant
    ``plant_copies`` how many identical cells/shifts of the blueprint plant
                     are built (a bigger plant for the bigger dataset)
    ``material_factor`` multiplier for the raw material stock and receipts
    """

    orders: int
    plant_copies: int
    material_factor: int


SCALE_PROFILES: dict[str, ScaleProfile] = {
    "small": ScaleProfile(orders=6, plant_copies=1, material_factor=1),
    "medium": ScaleProfile(orders=20, plant_copies=1, material_factor=1),
    "large": ScaleProfile(orders=60, plant_copies=2, material_factor=2),
}

#: Sequence dependent setup minutes.  The matrix satisfies the triangle
#: inequality (checked by ``tests/test_setup_matrix.py``).
SETUP_MATRIX: dict[tuple[str, str], int] = {
    ("A", "A"): 0,
    ("A", "B"): 30,
    ("A", "C"): 45,
    ("B", "A"): 30,
    ("B", "B"): 0,
    ("B", "C"): 30,
    ("C", "A"): 45,
    ("C", "B"): 30,
    ("C", "C"): 0,
}

MACHINE_BLUEPRINT: list[dict[str, Any]] = [
    {
        "code": "CNC-01",
        "name": "CNC Mill 1",
        "type": "CNC",
        "families": ["A", "B", "C"],
        "cost": 95.0,
    },
    {
        "code": "CNC-02",
        "name": "CNC Mill 2",
        "type": "CNC",
        "families": ["A", "B", "C"],
        "cost": 95.0,
    },
    {"code": "CNC-03", "name": "CNC Lathe 3", "type": "CNC", "families": ["A", "B"], "cost": 80.0},
    {
        "code": "PRESS-01",
        "name": "Hydraulic Press 1",
        "type": "PRESS",
        "families": ["B", "C"],
        "cost": 70.0,
    },
    {
        "code": "WELD-01",
        "name": "Weld Cell 1",
        "type": "WELD",
        "families": ["A", "B", "C"],
        "cost": 60.0,
    },
    {
        "code": "WELD-02",
        "name": "Weld Cell 2",
        "type": "WELD",
        "families": ["A", "B", "C"],
        "cost": 60.0,
    },
    {
        "code": "FURNACE-01",
        "name": "Heat Treat Furnace",
        "type": "FURNACE",
        "families": ["B"],
        "cost": 120.0,
        "continuous": True,
    },
    {
        "code": "ASSY-01",
        "name": "Assembly Line A",
        "type": "ASSY",
        "families": ["A", "B", "C"],
        "cost": 55.0,
    },
    {
        "code": "ASSY-02",
        "name": "Assembly Line B",
        "type": "ASSY",
        "families": ["A", "B", "C"],
        "cost": 55.0,
    },
    {
        "code": "PACK-01",
        "name": "Packaging Line 1",
        "type": "PACK",
        "families": ["A", "B", "C"],
        "cost": 40.0,
    },
    {
        "code": "INSP-01",
        "name": "CMM Inspection",
        "type": "INSP",
        "families": ["A", "B", "C"],
        "cost": 65.0,
    },
]

OPERATOR_BLUEPRINT: list[dict[str, Any]] = [
    {"code": "OP-01", "name": "Ana Ferreira", "skills": ["cnc-op"], "types": ["CNC"], "cost": 42.0},
    {
        "code": "OP-02",
        "name": "Ben Kaur",
        "skills": ["cnc-op", "milling"],
        "types": ["CNC"],
        "cost": 46.0,
    },
    {"code": "OP-03", "name": "Cara Osei", "skills": ["welding"], "types": ["WELD"], "cost": 44.0},
    {
        "code": "OP-04",
        "name": "Dan Petrov",
        "skills": ["welding", "press"],
        "types": ["WELD", "PRESS"],
        "cost": 47.0,
    },
    {"code": "OP-05", "name": "Eve Nowak", "skills": ["assembly"], "types": ["ASSY"], "cost": 38.0},
    {
        "code": "OP-06",
        "name": "Finn Okafor",
        "skills": ["assembly", "packing"],
        "types": ["ASSY", "PACK"],
        "cost": 39.0,
    },
    {"code": "OP-07", "name": "Gita Rao", "skills": ["quality"], "types": ["INSP"], "cost": 45.0},
    {
        "code": "OP-08",
        "name": "Hugo Marek",
        "skills": ["furnace", "packing"],
        "types": ["FURNACE", "PACK"],
        "cost": 41.0,
    },
    {
        "code": "OP-09",
        "name": "Ines Costa",
        "skills": ["press", "welding"],
        "types": ["PRESS", "WELD"],
        "cost": 43.0,
    },
    {
        "code": "OP-10",
        "name": "Jonas Berg",
        "skills": ["cnc-op", "milling", "quality"],
        "types": ["CNC", "INSP"],
        "cost": 48.0,
    },
]

PRODUCT_BLUEPRINT: list[dict[str, Any]] = [
    {
        "sku": "PMP-100",
        "name": "Precision Pump 100",
        "family": "A",
        "unit_cost": 240.0,
        "operations": [
            ("Turning", "CNC", 0.8, 20, ["cnc-op"]),
            ("Milling", "CNC", 1.2, 30, ["cnc-op", "milling"]),
            ("Weld Housing", "WELD", 0.9, 15, ["welding"]),
            ("Final Assembly", "ASSY", 1.5, 45, ["assembly"]),
            ("Final Inspection", "INSP", 0.4, 10, ["quality"]),
        ],
        "materials": {"Turning": {"RM-ALU": 1.0}, "Milling": {"RM-ALU": 0.8}},
    },
    {
        "sku": "VLV-200",
        "name": "Control Valve 200",
        "family": "B",
        "unit_cost": 410.0,
        "operations": [
            ("Casting", "FURNACE", 1.8, 45, ["furnace"]),
            ("Turning", "CNC", 0.9, 20, ["cnc-op"]),
            ("Weld Body", "WELD", 1.1, 20, ["welding"]),
            ("Final Assembly", "ASSY", 1.6, 40, ["assembly"]),
            ("Packaging", "PACK", 0.5, 10, ["packing"]),
        ],
        "materials": {
            "Casting": {"RM-STEEL": 2.0},
            "Weld Body": {"RM-STEEL": 0.5},
            "Packaging": {"RM-PKG": 1.0},
        },
    },
    {
        "sku": "HUB-300",
        "name": "Drive Hub 300",
        "family": "C",
        "unit_cost": 175.0,
        "operations": [
            ("Forging", "PRESS", 1.4, 25, ["press"]),
            ("Milling", "CNC", 1.3, 30, ["cnc-op", "milling"]),
            ("Sub Assembly", "ASSY", 1.2, 30, ["assembly"]),
            ("Final Inspection", "INSP", 0.5, 10, ["quality"]),
        ],
        "materials": {"Forging": {"RM-STEEL": 1.5}, "Sub Assembly": {"RM-SEAL": 1.0}},
    },
]

MATERIAL_BLUEPRINT: list[dict[str, Any]] = [
    {
        "sku": "RM-ALU",
        "name": "Aluminium Billet",
        "unit": "kg",
        "on_hand": 500.0,
        "safety": 100.0,
        "cost": 6.5,
    },
    {
        "sku": "RM-STEEL",
        "name": "Steel Bar",
        "unit": "kg",
        "on_hand": 700.0,
        "safety": 150.0,
        "cost": 4.2,
    },
    {
        "sku": "RM-SEAL",
        "name": "Seal Kit",
        "unit": "pcs",
        "on_hand": 250.0,
        "safety": 40.0,
        "cost": 11.0,
    },
    {
        "sku": "RM-PKG",
        "name": "Packaging Set",
        "unit": "pcs",
        "on_hand": 400.0,
        "safety": 60.0,
        "cost": 2.4,
    },
]

CUSTOMER_BLUEPRINT: list[dict[str, Any]] = [
    {"code": "CUST-NORTHWIND", "name": "Northwind Energy", "tier": 1},
    {"code": "CUST-VECTRA", "name": "Vectra Marine", "tier": 2},
    {"code": "CUST-HELIOS", "name": "Helios Pumps", "tier": 2},
    {"code": "CUST-ARCTIC", "name": "Arctic Mining Co", "tier": 3},
]


def _clear(session: Session) -> None:
    """Delete every generated row, children first (FK constraints are enforced).

    The AI provider store and the global AI policy are part of the demo state
    as well: leaving them behind would keep stale credentials in the database
    and would let a provider that the factory does not create stay the default
    one, which would make the demo behave differently on every reset.
    """
    for model in (
        AIMessage,
        AIConversation,
        AIProvider,
        AISettings,
        ScheduleOperation,
        OptimizationRun,
        Schedule,
        Scenario,
        MaterialRequirement,
        RoutingOperation,
        WorkOrder,
        Routing,
        Product,
        MaterialReceipt,
        Material,
        MachineSetup,
        MachineDowntime,
        MachineCalendarException,
        Machine,
        OperatorAbsence,
        Operator,
        CalendarBreak,
        CalendarException,
        CalendarShift,
        Calendar,
        Customer,
        OperationalEvent,
        Job,
        AuditLog,
    ):
        session.execute(delete(model))
    session.flush()


def _build_calendars(session: Session, reference: date) -> Calendar:
    std = Calendar(
        code="PLANT-STD",
        name="Standard two-shift plant calendar",
        timezone="UTC",
        is_plant_calendar=True,
        description="Monday to Friday, 06:00-22:00 in two shifts.",
    )
    continuous = Calendar(
        code="PLANT-24X7",
        name="Continuous furnace calendar",
        timezone="UTC",
        is_plant_calendar=True,
        is_continuous=True,
        description="Always available (heat treatment furnace).",
    )
    session.add_all([std, continuous])
    session.flush()
    for day in range(1, 6):  # Monday .. Friday
        session.add(
            CalendarShift(
                calendar_id=std.id,
                day_of_week=day,
                start_minute=6 * 60,
                end_minute=14 * 60,
                label="Day",
            )
        )
        session.add(
            CalendarShift(
                calendar_id=std.id,
                day_of_week=day,
                start_minute=14 * 60,
                end_minute=22 * 60,
                label="Swing",
            )
        )
    # Plant shutdown: the Sunday before the reference week is non working.
    session.add(
        CalendarException(
            calendar_id=std.id,
            day=reference - timedelta(days=1),
            kind="shutdown",
            label="Maintenance shutdown",
        )
    )
    session.flush()
    return std


def _build_machines(
    session: Session,
    std: Calendar,
    continuous: Calendar,
    copies: int = 1,
) -> dict[str, Machine]:
    """Build the machine fleet.

    ``copies`` replicates the blueprint plant (a second and third cell of the
    same type) so that the larger datasets have enough capacity to be
    schedulable at all.
    """
    machines: dict[str, Machine] = {}
    for copy_index in range(1, copies + 1):
        suffix = "" if copy_index == 1 else f"-C{copy_index}"
        for spec in MACHINE_BLUEPRINT:
            machine = Machine(
                code=f"{spec['code']}{suffix}",
                name=spec["name"] if copy_index == 1 else f"{spec['name']} (cell {copy_index})",
                machine_type=spec["type"],
                supported_families=list(spec["families"]),
                capabilities=[spec["type"].lower()],
                calendar_id=continuous.id if spec.get("continuous") else std.id,
                hourly_cost=spec["cost"],
                initial_setup_minutes=0,
                throughput_per_hour=60.0,
            )
            session.add(machine)
            machines[machine.code] = machine
    session.flush()
    for machine in machines.values():
        if machine.machine_type in {"CNC", "WELD", "ASSY"}:
            for (from_family, to_family), minutes in SETUP_MATRIX.items():
                session.add(
                    MachineSetup(
                        machine_id=machine.id,
                        from_family=from_family,
                        to_family=to_family,
                        setup_minutes=minutes,
                        setup_cost=minutes * (machine.hourly_cost / 60.0),
                    )
                )
    session.flush()
    return machines


def _build_operators(session: Session, std: Calendar, copies: int = 1) -> list[Operator]:
    """Build the operator pool (``copies`` shifts per blueprint operator)."""
    operators: list[Operator] = []
    for copy_index in range(1, copies + 1):
        suffix = "" if copy_index == 1 else f"-C{copy_index}"
        for spec in OPERATOR_BLUEPRINT:
            operator = Operator(
                code=f"{spec['code']}{suffix}",
                name=spec["name"] if copy_index == 1 else f"{spec['name']} (shift {copy_index})",
                skills=list(spec["skills"]),
                certifications=["ISO-9001"] if "quality" in spec["skills"] else [],
                eligible_machine_types=list(spec["types"]),
                calendar_id=std.id,
                weekly_overtime_limit_minutes=600,
                weekly_regular_minutes=2400,
                hourly_cost=spec["cost"],
            )
            session.add(operator)
            operators.append(operator)
    session.flush()
    return operators


#: Purchase orders of the demo plant: (material, day offset, quantity, reference)
RECEIPT_PLAN: list[tuple[str, int, float, str]] = [
    ("RM-ALU", 3, 700.0, "PO-1041"),
    ("RM-ALU", 8, 800.0, "PO-1047"),
    ("RM-STEEL", 4, 900.0, "PO-1043"),
    ("RM-STEEL", 9, 900.0, "PO-1049"),
    ("RM-SEAL", 2, 300.0, "PO-1039"),
    ("RM-SEAL", 7, 500.0, "PO-1045"),
    ("RM-PKG", 5, 500.0, "PO-1042"),
]


def _receipt_plan(factor: int) -> list[tuple[str, int, float, str]]:
    """Scale the purchase orders with the dataset.

    Larger datasets receive additional deliveries later in the horizon so
    that the time-phased material availability stays satisfiable.
    """
    plan = list(RECEIPT_PLAN)
    if factor > 1:
        plan.extend(
            (sku, day + 5, quantity * factor, f"{reference}-R{index + 1}")
            for index, (sku, day, quantity, reference) in enumerate(RECEIPT_PLAN)
        )
    return [(sku, day, quantity * factor, reference) for sku, day, quantity, reference in plan]


def _build_materials(session: Session, now: datetime, factor: int = 1) -> dict[str, Material]:
    materials: dict[str, Material] = {}
    for spec in MATERIAL_BLUEPRINT:
        material = Material(
            sku=spec["sku"],
            name=spec["name"],
            unit=spec["unit"],
            on_hand=spec["on_hand"] * factor,
            safety_stock=spec["safety"] * factor,
            cost_per_unit=spec["cost"],
        )
        session.add(material)
        materials[spec["sku"]] = material
    session.flush()
    receipts = _receipt_plan(factor)
    for sku, day_offset, quantity, reference in receipts:
        session.add(
            MaterialReceipt(
                material_id=materials[sku].id,
                expected_at=now + timedelta(days=day_offset),
                quantity=quantity,
                reference=reference,
            )
        )
    session.flush()
    return materials


def _build_catalog(
    session: Session, now: datetime
) -> tuple[dict[str, Product], dict[str, Routing], list[Customer]]:
    customers: list[Customer] = []
    for spec in CUSTOMER_BLUEPRINT:
        customer = Customer(code=spec["code"], name=spec["name"], priority_tier=spec["tier"])
        session.add(customer)
        customers.append(customer)
    session.flush()

    products: dict[str, Product] = {}
    routings: dict[str, Routing] = {}
    for spec in PRODUCT_BLUEPRINT:
        product = Product(
            sku=spec["sku"],
            name=spec["name"],
            family=spec["family"],
            unit="pcs",
            unit_cost=spec["unit_cost"],
        )
        session.add(product)
        products[spec["sku"]] = product
        session.flush()
        routing = Routing(
            code=f"RT-{spec['sku']}",
            name=f"Standard routing {spec['name']}",
            product_id=product.id,
            version="1",
        )
        session.add(routing)
        routings[spec["sku"]] = routing
        session.flush()
        for index, (name, machine_type, per_unit, fixed, skills) in enumerate(
            spec["operations"], start=1
        ):
            operation = RoutingOperation(
                routing_id=routing.id,
                sequence=index,
                name=name,
                machine_type=machine_type,
                duration_per_unit_minutes=per_unit,
                fixed_minutes=fixed,
                minimum_minutes=15.0,
                transfer_time_minutes=10.0,
                required_skills=list(skills),
                setup_family=spec["family"],
                allow_alternative_machines=True,
                crew_size=1,
                hourly_cost=0.0,
            )
            session.add(operation)
            session.flush()
    session.flush()
    return products, routings, customers


def seed_demo_data(
    session: Session,
    scale: str = "medium",
    seed: int = 20240517,
    clear: bool = True,
    now: datetime | None = None,
) -> dict[str, int]:
    """Create a complete, deterministic demonstration factory."""
    # A seeded PRNG keeps the synthetic data reproducible.  It is not used for
    # anything security relevant.
    rng = random.Random(seed)  # nosec B311
    now = now or planning_now()
    reference = now.date()

    if clear:
        _clear(session)
        session.flush()

    profile = SCALE_PROFILES.get(scale, SCALE_PROFILES["medium"])

    std = _build_calendars(session, reference)
    continuous = session.scalars(select(Calendar).filter_by(code="PLANT-24X7")).one()
    machines = _build_machines(session, std, continuous, copies=profile.plant_copies)
    operators = _build_operators(session, std, copies=profile.plant_copies)
    materials = _build_materials(session, now, factor=profile.material_factor)
    products, routings, customers = _build_catalog(session, now)

    # material requirements (needs material ids)
    for routing in routings.values():
        product = routing.product
        spec = next(p for p in PRODUCT_BLUEPRINT if p["sku"] == product.sku)
        for operation in routing.operations:
            for material_sku, quantity in spec.get("materials", {}).get(operation.name, {}).items():
                session.add(
                    MaterialRequirement(
                        operation_id=operation.id,
                        material_id=materials[material_sku].id,
                        quantity_per_unit=quantity,
                        scrap_percent=2.0,
                    )
                )
    session.flush()

    order_count = profile.orders

    priorities = [3, 4, 2, 5, 3, 4]
    order_rows: list[WorkOrder] = []
    for index in range(order_count):
        product_spec = PRODUCT_BLUEPRINT[index % len(PRODUCT_BLUEPRINT)]
        product = products[product_spec["sku"]]
        routing = routings[product_spec["sku"]]
        customer = customers[index % len(customers)]
        quantity = rng.choice([15, 20, 25, 30, 40, 50, 60, 80, 100])
        priority = priorities[index % len(priorities)]
        release_offset = rng.choice([-2, -1, 0, 0, 1, 2])
        due_offset = rng.choice([2, 3, 4, 5, 6, 7, 9, 11])
        release = now + timedelta(days=release_offset, hours=rng.choice([0, 6, 8]))
        due = now + timedelta(days=due_offset, hours=rng.choice([0, 8, 12]))
        hard_deadline = None
        if index % 7 == 3:
            # Every fourth order carries a *mandatory* delivery commitment.
            # It is released early, has the top priority and enough slack so
            # that a competent plan (and the FCFS baseline) can meet it.
            priority = 5
            release = now - timedelta(days=1, hours=6)
            due = now + timedelta(days=6, hours=12)
            hard_deadline = due - timedelta(hours=4)
        order = WorkOrder(
            code=f"WO-{reference.strftime('%y%m%d')}-{index + 1:04d}",
            customer_id=customer.id,
            product_id=product.id,
            routing_id=routing.id,
            quantity=float(quantity),
            priority=priority,
            release_at=release,
            due_at=due,
            hard_deadline=hard_deadline,
            status="released",
            penalty_per_hour=25.0 * priority,
            is_urgent=(index == 0),
        )
        session.add(order)
        order_rows.append(order)
    session.flush()

    # planned maintenance windows
    for code, day_offset, hours in (("CNC-01", 3, 4), ("WELD-01", 5, 3), ("ASSY-01", 2, 2)):
        machine = machines[code]
        start = (now + timedelta(days=day_offset)).replace(hour=6, minute=0)
        session.add(
            MachineDowntime(
                machine_id=machine.id,
                starts_at=start,
                ends_at=start + timedelta(hours=hours),
                kind="maintenance",
                reason="Planned preventive maintenance",
            )
        )
    # one operator absence
    session.add(
        OperatorAbsence(
            operator_id=operators[4].id,
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=3),
            reason="Annual leave",
        )
    )
    session.flush()

    # ------------------------------------------------------------------
    # Offline AI provider (mock) so the assistant is demonstrable without
    # any external credentials.  It never fabricates a schedule: it only
    # restates the grounded evidence pack produced by the solver.
    # ------------------------------------------------------------------
    # The provider store and the policy are reset to the documented demo
    # state, so a reset always produces the same, reproducible setup.
    session.execute(delete(AIProvider))
    session.execute(delete(AISettings))
    session.flush()
    session.add(
        AISettings(
            id=1,
            enabled=True,
            allow_paid_providers=True,
            allow_local_providers=True,
            monthly_budget_usd=10.0,
            spend_limit_usd=25.0,
            max_cost_per_request_usd=0.25,
            default_model="mock-grounded",
        )
    )
    session.add(
        AIProvider(
            name="Offline mock",
            kind="mock",
            model="mock-grounded",
            base_url=None,
            api_key_encrypted=None,
            is_enabled=True,
            is_default=True,
            request_timeout_seconds=10.0,
            max_tokens=1500,
            temperature=0.0,
            max_retries=1,
            notes=(
                "Deterministic offline provider used for demos and tests. "
                "It only restates the grounded evidence pack."
            ),
        )
    )
    session.flush()

    # The number of *scheduled* operations is the number of routing steps of
    # every work order — that is what the scheduler sees as tasks.
    scheduled_operations = sum(len(routing.operations) for routing in routings.values())

    return {
        "customers": len(customers),
        "products": len(products),
        "routings": len(routings),
        "machines": len(machines),
        "operators": len(operators),
        "materials": len(materials),
        "work_orders": len(order_rows),
        "routing_steps": scheduled_operations,
        "operations": sum(
            len(order.routing.operations) for order in order_rows if order.routing is not None
        ),
    }
