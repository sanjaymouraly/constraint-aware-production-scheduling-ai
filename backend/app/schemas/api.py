"""Pydantic request/response schemas for the REST API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import default_worker_count


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user: UserOut


class UserOut(ORMModel):
    id: int
    username: str
    email: str
    full_name: str | None = None
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=256)
    full_name: str | None = Field(default=None, max_length=160)
    role: Literal["admin", "planner", "viewer"] = "viewer"


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class CustomerOut(ORMModel):
    id: int
    code: str
    name: str
    priority_tier: int
    notes: str | None = None


class CustomerIn(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=2, max_length=160)
    priority_tier: int = Field(default=3, ge=1, le=5)
    notes: str | None = None


class ProductOut(ORMModel):
    id: int
    sku: str
    name: str
    family: str
    unit: str
    unit_cost: float
    is_active: bool


class RoutingOut(ORMModel):
    id: int
    code: str
    name: str
    product_id: int
    version: str
    operations: list[RoutingOperationOut] = []


class RoutingOperationOut(ORMModel):
    id: int
    routing_id: int
    sequence: int
    name: str
    machine_type: str
    duration_per_unit_minutes: float
    fixed_minutes: float
    batch_size: int | None
    batch_setup_minutes: float
    transfer_time_minutes: float
    required_skills: list[str]
    preferred_machine_ids: list[int]
    allow_alternative_machines: bool
    is_parallel_group: str | None
    setup_family: str | None
    crew_size: int


class RoutingOperationUpdate(BaseModel):
    duration_per_unit_minutes: float | None = Field(default=None, ge=0)
    fixed_minutes: float | None = Field(default=None, ge=0)
    batch_size: int | None = Field(default=None, ge=1)
    batch_setup_minutes: float | None = Field(default=None, ge=0)
    transfer_time_minutes: float | None = Field(default=None, ge=0)
    required_skills: list[str] | None = None
    preferred_machine_ids: list[int] | None = None
    allow_alternative_machines: bool | None = None
    is_parallel_group: str | None = Field(default=None, max_length=32)
    setup_family: str | None = Field(default=None, max_length=48)
    crew_size: int | None = Field(default=None, ge=1)


# ---------------------------------------------------------------------------
# Work orders
# ---------------------------------------------------------------------------


class WorkOrderOut(ORMModel):
    id: int
    code: str
    customer_id: int
    product_id: int
    routing_id: int
    quantity: float
    priority: int
    release_at: datetime
    due_at: datetime
    hard_deadline: datetime | None
    status: str
    penalty_per_hour: float
    is_urgent: bool
    notes: str | None
    created_at: datetime
    customer_name: str | None = None
    product_sku: str | None = None


class WorkOrderIn(BaseModel):
    code: str | None = Field(default=None, max_length=48)
    customer_id: int
    product_id: int
    routing_id: int | None = None
    quantity: float = Field(gt=0, le=1_000_000)
    priority: int = Field(default=3, ge=1, le=5)
    release_at: datetime
    due_at: datetime
    hard_deadline: datetime | None = None
    status: Literal["planned", "released", "in_progress", "completed", "cancelled"] = "released"
    penalty_per_hour: float = Field(default=0.0, ge=0)
    is_urgent: bool = False
    notes: str | None = None

    @field_validator("due_at")
    @classmethod
    def _due_after_release(cls, value: datetime, info):
        release = info.data.get("release_at")
        if release and value <= release:
            raise ValueError("due_at must be after release_at")
        return value


class WorkOrderUpdate(BaseModel):
    quantity: float | None = Field(default=None, gt=0)
    priority: int | None = Field(default=None, ge=1, le=5)
    release_at: datetime | None = None
    due_at: datetime | None = None
    hard_deadline: datetime | None = None
    status: Literal["planned", "released", "in_progress", "completed", "cancelled"] | None = None
    penalty_per_hour: float | None = Field(default=None, ge=0)
    is_urgent: bool | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class MachineOut(ORMModel):
    id: int
    code: str
    name: str
    machine_type: str
    supported_families: list[str]
    capabilities: list[str]
    calendar_id: int
    hourly_cost: float
    initial_setup_minutes: float
    is_active: bool
    calendar_code: str | None = None
    downtime_count: int = 0
    setup_count: int = 0


class MachineIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=120)
    machine_type: str = Field(min_length=1, max_length=48)
    supported_families: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    calendar_id: int
    hourly_cost: float = Field(default=0.0, ge=0)
    initial_setup_minutes: float = Field(default=0.0, ge=0)
    is_active: bool = True


class MachineUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    supported_families: list[str] | None = None
    capabilities: list[str] | None = None
    calendar_id: int | None = None
    hourly_cost: float | None = Field(default=None, ge=0)
    initial_setup_minutes: float | None = Field(default=None, ge=0)
    is_active: bool | None = None


class MachineDowntimeIn(BaseModel):
    starts_at: datetime
    ends_at: datetime
    kind: Literal["maintenance", "breakdown", "planned_stop", "overhaul"] = "maintenance"
    reason: str | None = Field(default=None, max_length=240)
    is_locked: bool = False

    @model_validator(mode="after")
    def _valid_window(self) -> MachineDowntimeIn:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class OperatorOut(ORMModel):
    id: int
    code: str
    name: str
    skills: list[str]
    certifications: list[str]
    eligible_machine_types: list[str]
    calendar_id: int
    weekly_overtime_limit_minutes: int
    weekly_regular_minutes: int
    hourly_cost: float
    is_active: bool


class OperatorIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=120)
    skills: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    eligible_machine_types: list[str] = Field(default_factory=list)
    calendar_id: int
    weekly_overtime_limit_minutes: int = Field(default=600, ge=0)
    weekly_regular_minutes: int = Field(default=2400, ge=0)
    hourly_cost: float = Field(default=0.0, ge=0)
    is_active: bool = True


class OperatorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    skills: list[str] | None = None
    certifications: list[str] | None = None
    eligible_machine_types: list[str] | None = None
    calendar_id: int | None = None
    weekly_overtime_limit_minutes: int | None = Field(default=None, ge=0)
    weekly_regular_minutes: int | None = Field(default=None, ge=0)
    hourly_cost: float | None = Field(default=None, ge=0)
    is_active: bool | None = None


class OperatorAbsenceIn(BaseModel):
    starts_at: datetime
    ends_at: datetime
    reason: str | None = Field(default=None, max_length=160)
    is_locked: bool = False

    @model_validator(mode="after")
    def _valid_window(self) -> OperatorAbsenceIn:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class MaterialOut(ORMModel):
    id: int
    sku: str
    name: str
    unit: str
    on_hand: float
    safety_stock: float
    cost_per_unit: float
    is_active: bool
    receipts: list[MaterialReceiptOut] = []
    total_available: float = 0.0


class MaterialReceiptOut(ORMModel):
    id: int
    material_id: int
    expected_at: datetime
    quantity: float
    reference: str | None
    is_confirmed: bool


class MaterialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    unit: str | None = Field(default=None, min_length=1, max_length=24)
    on_hand: float | None = Field(default=None, ge=0)
    safety_stock: float | None = Field(default=None, ge=0)
    cost_per_unit: float | None = Field(default=None, ge=0)
    is_active: bool | None = None


class MaterialReceiptIn(BaseModel):
    expected_at: datetime
    quantity: float = Field(gt=0)
    reference: str | None = Field(default=None, max_length=80)
    is_confirmed: bool = False


class MaterialIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    unit: str = "kg"
    on_hand: float = Field(default=0.0, ge=0)
    safety_stock: float = Field(default=0.0, ge=0)
    cost_per_unit: float = Field(default=0.0, ge=0)
    is_active: bool = True


class CalendarExceptionIn(BaseModel):
    day: date
    kind: Literal["holiday", "shutdown", "extra_shift"]
    start_minute: int | None = Field(default=None, ge=0, le=2880)
    end_minute: int | None = Field(default=None, ge=1, le=2880)
    label: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _valid_window(self) -> CalendarExceptionIn:
        if (self.start_minute is None) != (self.end_minute is None):
            raise ValueError("start_minute and end_minute must be supplied together")
        if self.start_minute is not None and self.end_minute is not None:
            if self.end_minute <= self.start_minute:
                raise ValueError("end_minute must be after start_minute")
        return self


class CalendarOut(ORMModel):
    id: int
    code: str
    name: str
    timezone: str
    is_plant_calendar: bool
    is_continuous: bool
    description: str | None
    shifts: list[CalendarShiftOut] = []
    breaks: list[CalendarBreakOut] = []
    exceptions: list[CalendarExceptionOut] = []


class CalendarShiftOut(ORMModel):
    id: int
    day_of_week: int
    start_minute: int
    end_minute: int
    label: str | None
    is_overnight: bool


class CalendarBreakOut(ORMModel):
    id: int
    day_of_week: int
    start_minute: int
    end_minute: int
    label: str | None


class CalendarExceptionOut(ORMModel):
    id: int
    day: date
    kind: str
    start_minute: int | None
    end_minute: int | None
    label: str | None


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


class RunRequestModel(BaseModel):
    engine: Literal["fcfs", "cpsat"] = "cpsat"
    horizon_start: datetime | None = None
    horizon_end: datetime | None = None
    work_order_ids: list[int] | None = None
    customer_ids: list[int] | None = None
    machine_ids: list[int] | None = None
    time_limit_seconds: float = Field(default=20.0, gt=0, le=600)
    workers: int = Field(default_factory=default_worker_count, ge=1, le=32)
    random_seed: int = Field(default=42, ge=0)
    objective_name: Literal[
        "weighted_tardiness",
        "minimize_tardiness",
        "minimize_makespan",
        "minimize_setup",
        "minimize_cost",
        "balanced",
    ] = "weighted_tardiness"
    weights: dict[str, float] = Field(default_factory=dict)
    objective_mode: Literal["weighted", "lexicographic"] = "weighted"
    name: str | None = None
    notes: str | None = None
    publish: bool = False
    base_schedule_id: int | None = None
    stability_schedule_id: int | None = None
    scenario_id: int | None = None


class ScheduleOut(ORMModel):
    id: int
    code: str
    name: str
    version: int
    status: str
    source: str
    horizon_start: datetime
    horizon_end: datetime
    scenario_id: int | None
    parent_schedule_id: int | None
    is_published: bool
    published_at: datetime | None
    created_by: str | None
    created_at: datetime
    notes: str | None
    metrics: dict[str, Any]
    validation: dict[str, Any]


class ScheduleOperationOut(ORMModel):
    id: int
    schedule_id: int
    work_order_id: int
    routing_operation_id: int
    machine_id: int | None
    operator_id: int | None
    starts_at: datetime
    ends_at: datetime
    setup_starts_at: datetime | None
    setup_minutes: float
    processing_minutes: float
    is_locked: bool
    status: str
    work_order_code: str | None = None
    operation_name: str | None = None
    operation_sequence: int | None = None
    machine_code: str | None = None
    operator_code: str | None = None


class ScheduleDetail(ScheduleOut):
    operations: list[ScheduleOperationOut] = []


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


class ScenarioOut(ORMModel):
    id: int
    code: str
    name: str
    description: str | None
    base_schedule_id: int | None
    status: str
    assumptions: dict[str, Any]
    created_by: str | None
    created_at: datetime


class ScenarioCreate(BaseModel):
    base_schedule_id: int
    name: str = Field(min_length=2, max_length=160)
    description: str | None = None
    lock_started_operations: bool = True


class DisruptionIn(BaseModel):
    kind: Literal[
        "machine_breakdown",
        "material_delay",
        "urgent_order",
        "operator_absence",
        "unplanned_maintenance",
        "order_cancelled",
        "quantity_change",
        "demand_surge",
    ]
    title: str = ""
    description: str | None = None
    severity: Literal["info", "warning", "critical"] = "warning"
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------


class AIProviderIn(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    kind: Literal["openai", "anthropic", "openrouter", "custom", "ollama", "mock"]
    base_url: str | None = Field(default=None, max_length=400)
    model: str = Field(min_length=1, max_length=120)
    api_key: str | None = Field(default=None, max_length=400)
    is_enabled: bool = True
    is_default: bool = False
    request_timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    max_tokens: int = Field(default=2000, ge=64, le=32000)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    input_cost_per_1k: float | None = None
    output_cost_per_1k: float | None = None
    notes: str | None = None


class AIProviderOut(ORMModel):
    id: int
    name: str
    kind: str
    base_url: str | None
    model: str
    is_enabled: bool
    is_default: bool
    request_timeout_seconds: float
    max_tokens: int
    temperature: float
    max_retries: int
    input_cost_per_1k: float | None
    output_cost_per_1k: float | None
    notes: str | None
    last_test_at: datetime | None
    last_test_status: str | None
    last_test_message: str | None
    created_at: datetime
    updated_at: datetime
    has_api_key: bool = False
    masked_api_key: str | None = None


class AISettingsIn(BaseModel):
    enabled: bool = True
    allow_paid_providers: bool = True
    allow_local_providers: bool = True
    monthly_budget_usd: float = Field(default=10.0, ge=0)
    spend_limit_usd: float = Field(default=25.0, ge=0)
    max_cost_per_request_usd: float = Field(default=0.25, ge=0)
    default_model: str | None = None


class AISettingsOut(ORMModel):
    enabled: bool
    allow_paid_providers: bool
    allow_local_providers: bool
    monthly_budget_usd: float
    spend_limit_usd: float
    spent_usd: float
    max_cost_per_request_usd: float
    default_model: str | None
    updated_at: datetime


class AIExplainRequest(BaseModel):
    schedule_id: int
    question: str = Field(min_length=2, max_length=2000)
    provider_id: int | None = None
    baseline_schedule_id: int | None = None


LoginRequest.model_rebuild()
TokenResponse.model_rebuild()
RoutingOut.model_rebuild()
MaterialOut.model_rebuild()
CalendarOut.model_rebuild()
