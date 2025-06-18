# API reference

Base URL: `http://localhost:8000/api` (development).
Interactive documentation: <http://localhost:8000/docs> (Swagger UI) and
<http://localhost:8000/redoc>.

All routes are prefixed with `/api` (configurable through `API_PREFIX`).

---

## 1. Authentication and roles

```http
POST /api/auth/login
Content-Type: application/json

{ "username": "admin", "password": "admin" }
```

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "user": { "id": 1, "username": "admin", "role": "admin", "email": "admin@example.com" }
}
```

Send the token on every other request:

```http
Authorization: Bearer <jwt>
```

| Role | Capabilities |
| --- | --- |
| `admin` | Everything, including AI settings, provider keys and publishing. |
| `planner` | Run the optimiser, manage work orders/resources, publish schedules. |
| `viewer` | Read-only access to plans, Gantt, analytics and reports. |

| Route | Method | Role | Description |
| --- | --- | --- | --- |
| `/api/auth/login` | POST | anonymous | Exchange credentials for a JWT. |
| `/api/auth/me` | GET | any | The current user. |
| `/api/auth/users` | GET | admin | List users. |
| `/api/auth/users` | POST | admin | Create a user. |
| `/api/auth/users/{id}` | PATCH | admin | Change role, password or status. |
| `/api/auth/users/{id}` | DELETE | admin | Deactivate a user. |

---

## 2. Scheduling

### `POST /api/scheduling/run`

Runs the FCFS baseline or the CP-SAT optimiser and persists the result as a new
schedule version. Requires the `planner` or `admin` role.

```json
{
  "engine": "cpsat",
  "horizon_start": "2026-10-20T00:00:00Z",
  "horizon_end": "2026-11-03T00:00:00Z",
  "time_limit_seconds": 20,
  "workers": 2,
  "random_seed": 42,
  "objective_name": "weighted_tardiness",
  "weights": { "tardiness": 1.0, "makespan": 0.1, "setup": 0.05, "overtime": 0.2 },
  "name": "Nightly plan"
}
```

Response (abridged; `schedule` is a persisted `ScheduleOut`):

```json
{
  "status": "FEASIBLE",
  "solver_status": "FEASIBLE",
  "runtime_ms": 1832.4,
  "objective_value": 4611390,
  "best_bound": 4610130,
  "metrics": { "on_time_delivery_pct": 95.0, "weighted_tardiness": 4500.0, "...": "..." },
  "validation": {
    "valid": true,
    "checked_operations": 94,
    "violation_count": 0,
    "violations": [],
    "checked_constraints": ["H1 machine_eligibility", "H2 machine_capacity", "..."]
  },
  "schedule": { "id": 12, "code": "SCH-2026-10-18-0007", "is_published": false, "...": "..." }
}
```

* `status` ∈ `OPTIMAL | FEASIBLE | PARTIAL | INFEASIBLE | UNKNOWN | CANCELLED`.
  A time-limited run is **never** reported as `OPTIMAL`.
* `best_bound` is the objective bound the solver proved. When
  `status == "FEASIBLE"` and `objective_value == best_bound` the plan is
  optimal for the model that was solved.
* When more than `MAX_CONCURRENT_OPTIMIZATIONS` runs are active the endpoint
  answers **HTTP 429** with an explanatory message.

### `GET /api/scheduling/objectives`

Returns the objective presets, the default weights and the available terms
(`tardiness`, `makespan`, `setup`, `overtime`, `stability`, `flow`).

### Schedules

| Route | Method | Role | Description |
| --- | --- | --- | --- |
| `/api/schedules` | GET | any | List plan versions (filter by `status`, `source`, pagination). |
| `/api/schedules/{id}` | GET | any | One plan with metrics and validation report. |
| `/api/schedules/{id}/assignments` | GET | any | The scheduled operations. |
| `/api/schedules/{id}/gantt` | GET | any | Gantt payload (operations + horizon + machine list). |
| `/api/schedules/{id}/runs` | GET | any | The optimisation runs behind this version. |
| `/api/schedules/{id}/export.csv` | GET | any | CSV export of the plan. |
| `/api/schedules/{id}/publish` | POST | planner | Publish a plan (version history + rollback). |
| `/api/schedules/{id}` | DELETE | admin | Delete a draft plan version. |
| `/api/schedules/compare` | POST | any | Compare two or more plans metric by metric. |

```json
POST /api/schedules/compare
{ "schedule_ids": [11, 12] }
```

---

## 3. Background jobs

| Route | Method | Role | Description |
| --- | --- | --- | --- |
| `/api/jobs` | POST | planner | Queue a run; returns `job_id` immediately. |
| `/api/jobs` | GET | any | List jobs. |
| `/api/jobs/{id}` | GET | any | Job status, progress and result. |
| `/api/jobs/{id}/cancel` | POST | planner | Request cancellation of a running job. |

A job moves through `queued → running → succeeded | failed | cancelled`.
The result contains the schedule id, the metrics and the validation report.

---

## 4. Work orders and data exchange

| Route | Method | Role | Description |
| --- | --- | --- | --- |
| `/api/work-orders` | GET | any | List / filter / paginate work orders. |
| `/api/work-orders` | POST | planner | Create a work order. |
| `/api/work-orders/{id}` | GET | any | One work order with its operations. |
| `/api/work-orders/{id}` | PATCH | planner | Update (status, priority, quantity, dates …). |
| `/api/work-orders/{id}` | DELETE | planner | Cancel / delete a work order. |
| `/api/work-orders/template.csv` | GET | any | The import template. |
| `/api/work-orders/import` | POST | planner | CSV/JSON import with **row level** errors. |
| `/api/work-orders/export.csv` | GET | any | CSV export. |

The import endpoint never fails silently: it answers with the number of
imported, skipped and rejected rows plus one error message per row.

```json
{
  "rows": 21,
  "valid": 20,
  "errors": [
    { "row": 42, "message": "Unknown product 'PMP-999'.", "data": { "product_sku": "PMP-999" } }
  ],
  "created": 0,
  "dry_run": true
}
```

For safety, a non-dry-run import with any invalid row creates **zero** rows; fix the reported rows and submit the file again.

---

## 5. Resources

| Route | Method | Description |
| --- | --- | --- |
| `/api/machines` | GET / POST | Machines, types, capabilities, calendars, setups, downtimes. |
| `/api/machines/{id}` | PATCH | Update mutable machine fields (calendar, cost, families, active flag). |
| `/api/machines/{id}/setups` | GET | The sequence-dependent setup matrix of a machine. |
| `/api/machines/{id}/downtimes` | GET / POST | List or add maintenance, breakdown, planned-stop or overhaul windows. |
| `/api/operators` | GET / POST | Operators, skills, certifications, eligible machine types. |
| `/api/operators/{id}` | PATCH | Update mutable operator fields and the active flag. |
| `/api/operators/{id}/absences` | GET / POST | List or record an operator absence. |
| `/api/materials` | GET / POST | Materials, stock, safety stock. |
| `/api/materials/{id}` | PATCH | Update stock, costs, safety stock or active status. |
| `/api/materials/{id}/receipts` | POST | Add a time-phased purchase-order receipt. |
| `/api/calendars` | GET | Production calendars. |
| `/api/calendars/{id}/exceptions` | POST | Add a dated holiday, shutdown or extra shift. |
| `/api/calendars/{id}/segments` | GET | The expanded working windows of a calendar. |
| `/api/routings` | GET | Routings with their operations, alternatives, batch size/setup and parallel-group data. |
| `/api/routings/operations/{id}` | PATCH | Update transfer time, batch rules, skills, preferred machines, alternatives, parallel group or crew size. |
| `/api/products` | GET | The product catalogue. |
| `/api/customers` | GET / POST | Customers and their priority tier. |

---

## 6. Scenarios

| Route | Method | Role | Description |
| --- | --- | --- | --- |
| `/api/scenarios` | GET | any | List scenarios. |
| `/api/scenarios` | POST | planner | Clone a plan into a new scenario. |
| `/api/scenarios/{id}` | GET | any | Scenario detail with its disruptions. |
| `/api/scenarios/{id}/disruptions` | POST | planner | Add a disruption (8 kinds). |
| `/api/scenarios/{id}/simulate` | POST | planner | Re-plan the scenario with the disruptions applied. |
| `/api/scenarios/{id}/impact` | GET | any | Compare the scenario plan with the live plan. |
| `/api/scenarios/{id}/publish` | POST | planner | Publish the scenario plan explicitly. |

Disruption payload example:

```json
{
  "kind": "machine_breakdown",
  "title": "CNC-02 spindle failure",
  "severity": "critical",
  "payload": { "machine_id": 3, "duration_hours": 8 }
}
```

Supported kinds: `machine_breakdown`, `material_delay`, `operator_absence`,
`maintenance_window`, `urgent_order`, `order_cancelled`, `quantity_change`,
`demand_surge`.

---

## 7. AI subsystem

| Route | Method | Role | Description |
| --- | --- | --- | --- |
| `/api/ai/catalog` | GET | any | The supported provider kinds and their defaults. |
| `/api/ai/settings` | GET / PUT | admin | Enable/disable AI, default provider, budgets, limits. |
| `/api/ai/providers` | GET / POST | admin | List and register providers (the key is write-only). |
| `/api/ai/providers/{id}` | PATCH / DELETE | admin | Update or remove a provider. |
| `/api/ai/providers/{id}/models` | GET | admin | Model discovery (live for hosted providers). |
| `/api/ai/providers/{id}/test` | POST | admin | Connectivity test (latency + error). |
| `/api/ai/explain` | POST | planner | Grounded explanation of a validated schedule. |

The explanation request carries a schedule id and a question; the response
contains the grounded answer, the bullets, the evidence references and the
list of `unsupported` claims that were detected and rejected.

---

## 8. System and audit

| Route | Method | Description |
| --- | --- | --- |
| `/api/health` | GET | Liveness probe. |
| `/api/ready` | GET | Readiness probe (database reachable). |
| `/api/dashboard` | GET | Aggregated KPIs for the overview screen. |
| `/api/events` | GET | Operational events (recent activity). |
| `/api/audit` | GET | The immutable audit log (admin). |

---

## 9. Conventions

* **Errors** are returned as `{"detail": "..."}` with the appropriate status
  code. Validation errors from Pydantic use the standard FastAPI list format.
* **Timestamps** are ISO-8601 with an explicit UTC offset.
* **Durations** are minutes (integers).
* List endpoints accept `limit` / `offset` where documented and return the
  response shape in the endpoint's OpenAPI schema (many return a raw JSON list).
* **Rate limiting** returns `429` with a `Retry-After`-style message when the
  per-client window is exhausted.
