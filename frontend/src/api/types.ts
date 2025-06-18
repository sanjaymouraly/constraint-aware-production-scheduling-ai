export type Role = 'admin' | 'planner' | 'viewer'

export interface User {
  id: number
  username: string
  email: string
  full_name?: string | null
  role: Role
  is_active: boolean
  created_at: string
  last_login_at?: string | null
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in_minutes: number
  user: User
}

export interface WorkOrder {
  id: number
  code: string
  customer_id: number
  product_id: number
  routing_id: number
  quantity: number
  priority: number
  release_at: string
  due_at: string
  hard_deadline?: string | null
  status: string
  penalty_per_hour: number
  is_urgent: boolean
  notes?: string | null
  created_at: string
  customer_name?: string | null
  product_sku?: string | null
}

export interface RoutingOperation {
  id: number
  routing_id: number
  sequence: number
  name: string
  machine_type: string
  duration_per_unit_minutes: number
  fixed_minutes: number
  batch_size: number | null
  batch_setup_minutes: number
  transfer_time_minutes: number
  required_skills: string[]
  preferred_machine_ids: number[]
  allow_alternative_machines: boolean
  is_parallel_group: string | null
  setup_family: string | null
  crew_size: number
}

export interface Routing {
  id: number
  code: string
  name: string
  product_id: number
  version: string
  operations: RoutingOperation[]
}

export interface Machine {
  id: number
  code: string
  name: string
  machine_type: string
  supported_families: string[]
  capabilities: string[]
  calendar_id: number
  hourly_cost: number
  initial_setup_minutes: number
  is_active: boolean
  calendar_code?: string | null
  downtime_count: number
  setup_count: number
}

export interface Operator {
  id: number
  code: string
  name: string
  skills: string[]
  certifications: string[]
  eligible_machine_types: string[]
  calendar_id: number
  weekly_overtime_limit_minutes: number
  weekly_regular_minutes: number
  hourly_cost: number
  is_active: boolean
}

export interface MaterialReceipt {
  id: number
  material_id: number
  expected_at: string
  quantity: number
  reference?: string | null
  is_confirmed: boolean
}

export interface Material {
  id: number
  sku: string
  name: string
  unit: string
  on_hand: number
  safety_stock: number
  cost_per_unit: number
  is_active: boolean
  receipts: MaterialReceipt[]
  total_available: number
}

export interface Calendar {
  id: number
  code: string
  name: string
  timezone: string
  is_plant_calendar: boolean
  is_continuous: boolean
  description?: string | null
  shifts: CalendarShift[]
  breaks: CalendarBreak[]
  exceptions: CalendarException[]
}

export interface CalendarShift {
  id: number
  day_of_week: number
  start_minute: number
  end_minute: number
  label?: string | null
  is_overnight: boolean
}

export interface CalendarBreak {
  id: number
  day_of_week: number
  start_minute: number
  end_minute: number
  label?: string | null
}

export interface CalendarException {
  id: number
  day: string
  kind: string
  start_minute?: number | null
  end_minute?: number | null
  label?: string | null
}

export interface ScheduleOperation {
  id: number
  schedule_id: number
  work_order_id: number
  work_order_code?: string | null
  routing_operation_id: number
  operation_name?: string | null
  operation_sequence?: number | null
  machine_id: number
  machine_code?: string | null
  operator_id?: number | null
  operator_code?: string | null
  starts_at: string
  ends_at: string
  setup_starts_at?: string | null
  setup_minutes: number
  processing_minutes: number
  is_locked: boolean
  status: string
}

export interface Schedule {
  id: number
  code: string
  name: string
  version: number
  status: string
  source: string
  horizon_start: string
  horizon_end: string
  scenario_id?: number | null
  parent_schedule_id?: number | null
  is_published: boolean
  published_at?: string | null
  created_by?: string | null
  created_at: string
  notes?: string | null
  metrics: Record<string, any>
  validation: {
    valid?: boolean
    error_count?: number
    warning_count?: number
    violations?: any[]
  }
  operations?: ScheduleOperation[]
}

export interface MachineMetric {
  machine_id: number
  machine_code: string
  machine_name: string
  machine_type: string
  busy_minutes: number
  setup_minutes: number
  available_minutes: number
  utilisation_pct: number
  operations: number
}

export interface OrderMetric {
  work_order_id: number
  work_order_code: string
  priority: number
  due_at: string
  completion: string
  tardiness_minutes: number
  is_late: boolean
  hard_deadline?: string | null
}

export interface Metrics {
  on_time_delivery_pct: number
  orders_scheduled: number
  orders_on_time: number
  orders_late: number
  tardiness_minutes: number
  tardiness_hours: number
  weighted_tardiness: number
  penalty_cost: number
  makespan_minutes: number
  makespan_hours: number
  plan_start: string
  plan_end: string
  operations_scheduled: number
  operations_total: number
  throughput_orders: number
  setup_minutes: number
  setup_hours: number
  machine_busy_minutes: number
  machine_available_minutes: number
  machine_utilisation_pct: number
  overtime_minutes: number
  overtime_hours: number
  labour_cost: number
  machine_cost: number
  operating_cost: number
  displaced_operations: number
  moved_operations: number
  schedule_disruption_pct: number
  machines: MachineMetric[]
  operators: any[]
  orders: OrderMetric[]
  bottleneck_machine?: MachineMetric | null
  solver_runtime_ms?: number
}

export interface ComparisonRow {
  metric: string
  baseline: number
  optimised: number
  delta: number
  delta_pct: number | null
  direction: 'up' | 'down'
  verdict: 'better' | 'equal' | 'worse'
}

export interface ValidationReport {
  valid: boolean
  checked_operations: number
  violation_count: number
  error_count: number
  warning_count: number
  violations: {
    code: string
    severity: string
    message: string
    task_key?: string | null
    machine_id?: number | null
    operator_id?: number | null
    material_id?: number | null
    details?: Record<string, any>
  }[]
  checked_constraints: string[]
}

export interface RunResponse {
  status: string
  solver_status: string
  objective_value?: number | null
  best_bound?: number | null
  runtime_ms: number
  metrics: Metrics
  validation: ValidationReport
  diagnostics: any[]
  unscheduled: string[]
  schedule: Schedule | null
  baseline_metrics: Metrics | null
  comparison: { rows: ComparisonRow[]; improved_metrics: number; regressed_metrics: number } | null
  parameters: Record<string, any>
}

export interface Scenario {
  id: number
  code: string
  name: string
  description?: string | null
  base_schedule_id?: number | null
  status: string
  assumptions: {
    disruptions?: any[]
    base_schedule_id?: number
    clone_schedule_id?: number
    locked_operations?: number
  }
  created_by?: string | null
  created_at: string
}

export interface OperationalEvent {
  id: number
  kind: string
  severity: string
  title: string
  description?: string | null
  payload: Record<string, any>
  created_at: string
  created_by?: string | null
}

export interface AIProvider {
  id: number
  name: string
  kind: 'openai' | 'anthropic' | 'openrouter' | 'custom' | 'ollama' | 'mock'
  base_url?: string | null
  model: string
  is_enabled: boolean
  is_default: boolean
  request_timeout_seconds: number
  max_tokens: number
  temperature: number
  max_retries: number
  input_cost_per_1k?: number | null
  output_cost_per_1k?: number | null
  notes?: string | null
  last_test_at?: string | null
  last_test_status?: string | null
  last_test_message?: string | null
  created_at: string
  updated_at: string
  has_api_key: boolean
  masked_api_key?: string | null
}

export interface AISettings {
  enabled: boolean
  allow_paid_providers: boolean
  allow_local_providers: boolean
  monthly_budget_usd: number
  spend_limit_usd: number
  spent_usd: number
  max_cost_per_request_usd: number
  default_model?: string | null
  updated_at: string
}

export interface ProviderCatalogEntry {
  kind: string
  label: string
  default_base_url: string
  default_model: string
  suggested_models: string[]
  requires_key: boolean
  hosted: boolean
  supports_model_discovery: boolean
}
