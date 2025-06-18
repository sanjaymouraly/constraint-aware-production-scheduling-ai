"""HTTP API tests: authentication, RBAC, validation, scheduling and data exchange."""

from __future__ import annotations

import io

import pytest
from app.models import AIProvider, AISettings
from app.seed.demo_factory import seed_demo_data
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


class TestDemoFactory:
    """A demo reset must produce exactly the documented, reproducible state."""

    def test_reset_removes_stale_ai_providers_and_restores_the_default(
        self, session: Session
    ) -> None:
        session.add(
            AIProvider(
                name="Stale provider",
                kind="openai",
                model="gpt-4o-mini",
                base_url="https://api.openai.com/v1",
                api_key_encrypted="whatever",
                is_enabled=True,
                is_default=True,
            )
        )
        session.commit()

        seed_demo_data(session, scale="small", clear=True)
        session.commit()

        providers = session.query(AIProvider).all()
        assert [p.name for p in providers] == ["Offline mock"]
        assert providers[0].is_default is True
        assert providers[0].kind == "mock"

    def test_reset_restores_the_ai_policy(self, session: Session) -> None:
        settings = session.get(AISettings, 1)
        assert settings is not None
        settings.enabled = False
        settings.spent_usd = 12.5
        session.commit()

        seed_demo_data(session, scale="small", clear=True)
        session.commit()

        settings = session.get(AISettings, 1)
        assert settings is not None
        assert settings.enabled is True
        assert settings.spent_usd == 0.0
        assert settings.spend_limit_usd == 25.0

    def test_seed_reports_the_real_operation_count(self, session: Session) -> None:
        counts = seed_demo_data(session, scale="small", clear=True)
        session.commit()
        assert counts["work_orders"] == 6
        # The scheduler sees one task per routing step of every work order.
        assert counts["operations"] > counts["work_orders"]
        assert counts["operations"] == 28


class TestHealth:
    def test_health_is_public(self, client: TestClient) -> None:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_ready_reports_the_database(self, client: TestClient) -> None:
        response = client.get("/api/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["database"] in {"ok", "connected"}
        assert "database_latency_ms" in body

    def test_unknown_route_returns_404(self, client: TestClient) -> None:
        assert client.get("/api/does-not-exist").status_code == 404


class TestAuthentication:
    def test_login_with_valid_credentials(self, client: TestClient) -> None:
        response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["user"]["role"] == "admin"
        assert body["access_token"]

    def test_login_with_wrong_password(self, client: TestClient) -> None:
        response = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert response.status_code == 401

    def test_login_with_unknown_user(self, client: TestClient) -> None:
        response = client.post(
            "/api/auth/login", json={"username": "nobody", "password": "whatever"}
        )
        assert response.status_code == 401

    def test_login_requires_fields(self, client: TestClient) -> None:
        assert client.post("/api/auth/login", json={}).status_code == 422

    def test_protected_endpoint_requires_a_token(self, client: TestClient) -> None:
        assert client.get("/api/work-orders").status_code == 401

    def test_me_returns_the_current_user(self, client: TestClient, auth: dict) -> None:
        response = client.get("/api/auth/me", headers=auth)
        assert response.status_code == 200
        assert response.json()["username"] == "admin"

    def test_invalid_token_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/auth/me", headers={"Authorization": "Bearer nonsense"})
        assert response.status_code == 401


class TestRoleBasedAccessControl:
    def test_viewer_cannot_run_a_scheduling_run(
        self, client: TestClient, viewer_token: str
    ) -> None:
        response = client.post(
            "/api/scheduling/run",
            json={"engine": "fcfs"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert response.status_code == 403

    def test_viewer_can_read(self, client: TestClient, viewer_token: str) -> None:
        response = client.get(
            "/api/work-orders", headers={"Authorization": f"Bearer {viewer_token}"}
        )
        assert response.status_code == 200

    def test_viewer_cannot_manage_ai_providers(self, client: TestClient, viewer_token: str) -> None:
        response = client.get(
            "/api/ai/providers", headers={"Authorization": f"Bearer {viewer_token}"}
        )
        assert response.status_code == 403

    def test_planner_can_run_but_not_manage_ai(
        self, client: TestClient, planner_token: str
    ) -> None:
        headers = {"Authorization": f"Bearer {planner_token}"}
        run = client.post("/api/scheduling/run", json={"engine": "fcfs"}, headers=headers)
        assert run.status_code == 200
        assert client.get("/api/ai/providers", headers=headers).status_code == 403

    def test_admin_can_manage_ai_providers(self, client: TestClient, auth: dict) -> None:
        assert client.get("/api/ai/providers", headers=auth).status_code == 200

    def test_creating_a_user_requires_admin(self, client: TestClient, planner_token: str) -> None:
        response = client.post(
            "/api/auth/users",
            json={
                "username": "x1",
                "email": "x@example.com",
                "password": "secret-pass",
                "role": "viewer",
            },
            headers={"Authorization": f"Bearer {planner_token}"},
        )
        assert response.status_code == 403

    def test_weak_password_is_rejected(self, client: TestClient, auth: dict) -> None:
        response = client.post(
            "/api/auth/users",
            json={"username": "weak", "email": "w@example.com", "password": "short"},
            headers=auth,
        )
        assert response.status_code == 422


class TestWorkOrders:
    def test_list_work_orders(self, client: TestClient, auth: dict) -> None:
        response = client.get("/api/work-orders", headers=auth)
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 20
        assert "customer_name" in body[0]

    def test_filter_by_status(self, client: TestClient, auth: dict) -> None:
        response = client.get("/api/work-orders?status=released", headers=auth)
        assert response.status_code == 200
        assert all(row["status"] == "released" for row in response.json())

    def test_search(self, client: TestClient, auth: dict) -> None:
        response = client.get("/api/work-orders?search=WO-", headers=auth)
        assert response.status_code == 200
        assert len(response.json()) == 20

    def test_create_work_order(self, client: TestClient, auth: dict) -> None:
        products = client.get("/api/products", headers=auth).json()
        customers = client.get("/api/customers", headers=auth).json()
        response = client.post(
            "/api/work-orders",
            json={
                "customer_id": customers[0]["id"],
                "product_id": products[0]["id"],
                "quantity": 12,
                "priority": 2,
                "release_at": "2026-01-05T06:00:00Z",
                "due_at": "2026-01-10T06:00:00Z",
                "status": "released",
            },
            headers=auth,
        )
        assert response.status_code == 201, response.text
        assert response.json()["quantity"] == 12

    def test_due_date_must_be_after_release(self, client: TestClient, auth: dict) -> None:
        products = client.get("/api/products", headers=auth).json()
        customers = client.get("/api/customers", headers=auth).json()
        response = client.post(
            "/api/work-orders",
            json={
                "customer_id": customers[0]["id"],
                "product_id": products[0]["id"],
                "quantity": 1,
                "release_at": "2026-01-10T06:00:00Z",
                "due_at": "2026-01-05T06:00:00Z",
            },
            headers=auth,
        )
        assert response.status_code == 422

    def test_quantity_must_be_positive(self, client: TestClient, auth: dict) -> None:
        products = client.get("/api/products", headers=auth).json()
        customers = client.get("/api/customers", headers=auth).json()
        response = client.post(
            "/api/work-orders",
            json={
                "customer_id": customers[0]["id"],
                "product_id": products[0]["id"],
                "quantity": 0,
                "release_at": "2026-01-05T06:00:00Z",
                "due_at": "2026-01-10T06:00:00Z",
            },
            headers=auth,
        )
        assert response.status_code == 422

    def test_delete_work_order(self, client: TestClient, auth: dict) -> None:
        created = client.post(
            "/api/work-orders",
            json={
                "customer_id": client.get("/api/customers", headers=auth).json()[0]["id"],
                "product_id": client.get("/api/products", headers=auth).json()[0]["id"],
                "quantity": 3,
                "release_at": "2026-01-05T06:00:00Z",
                "due_at": "2026-01-10T06:00:00Z",
            },
            headers=auth,
        )
        work_order_id = created.json()["id"]
        assert client.delete(f"/api/work-orders/{work_order_id}", headers=auth).status_code == 204
        assert client.get(f"/api/work-orders/{work_order_id}", headers=auth).status_code == 404


class TestDataExchange:
    def test_template_csv_is_downloadable(self, client: TestClient, auth: dict) -> None:
        response = client.get("/api/work-orders/template.csv", headers=auth)
        assert response.status_code == 200
        assert "customer_code" in response.text
        assert "product_sku" in response.text

    def test_export_csv(self, client: TestClient, auth: dict) -> None:
        response = client.get("/api/work-orders/export.csv", headers=auth)
        assert response.status_code == 200
        assert response.text.count("\n") >= 21

    def test_import_dry_run_reports_row_errors(self, client: TestClient, auth: dict) -> None:
        payload = (
            "code,customer_code,product_sku,quantity,priority,release_at,due_at,"
            "hard_deadline,penalty_per_hour,is_urgent,status,notes\n"
            "WO-IMP-1,CUST-NORTHWIND,PMP-100,10,3,2026-01-05T06:00:00Z,"
            "2026-01-10T06:00:00Z,,50,false,released,imported\n"
            "WO-IMP-2,CUST-NORTHWIND,PMP-100,-5,3,2026-01-05T06:00:00Z,"
            "2026-01-10T06:00:00Z,,50,false,released,bad quantity\n"
        )
        response = client.post(
            "/api/work-orders/import?dry_run=true",
            files={"file": ("orders.csv", io.BytesIO(payload.encode()), "text/csv")},
            headers=auth,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] == 1
        assert body["errors"]
        assert body["errors"][0]["row"] == 3
        assert body["created"] == 0

    def test_import_creates_rows(self, client: TestClient, auth: dict) -> None:
        payload = (
            "code,customer_code,product_sku,quantity,priority,release_at,due_at,"
            "hard_deadline,penalty_per_hour,is_urgent,status,notes\n"
            "WO-IMP-OK,CUST-NORTHWIND,PMP-100,10,3,2026-01-05T06:00:00Z,"
            "2026-01-10T06:00:00Z,,50,false,released,imported\n"
        )
        response = client.post(
            "/api/work-orders/import",
            files={"file": ("orders.csv", io.BytesIO(payload.encode()), "text/csv")},
            headers=auth,
        )
        assert response.status_code == 200
        assert response.json()["created"] == 1

    def test_import_json_accepts_an_array_and_reports_row_errors(
        self, client: TestClient, auth: dict
    ) -> None:
        import json

        payload = [
            {
                "code": "WO-JSON-OK",
                "customer_code": "CUST-NORTHWIND",
                "product_sku": "PMP-100",
                "quantity": 10,
                "priority": 3,
                "release_at": "2026-11-01T06:00:00Z",
                "due_at": "2026-11-07T06:00:00Z",
            },
            {
                "code": "WO-JSON-BAD",
                "customer_code": "CUST-NORTHWIND",
                "product_sku": "PMP-100",
                "quantity": 0,
                "release_at": "2026-11-01T06:00:00Z",
                "due_at": "2026-11-07T06:00:00Z",
            },
        ]
        response = client.post(
            "/api/work-orders/import?dry_run=true",
            files={
                "file": (
                    "orders.json",
                    io.BytesIO(json.dumps(payload).encode()),
                    "application/json",
                )
            },
            headers=auth,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["valid"] == 1
        assert body["created"] == 0
        assert body["errors"][0]["row"] == 2

    def test_import_json_accepts_a_work_orders_envelope(
        self, client: TestClient, auth: dict
    ) -> None:
        import json

        payload = {
            "work_orders": [
                {
                    "code": "WO-JSON-ENVELOPE",
                    "customer_code": "CUST-NORTHWIND",
                    "product_sku": "PMP-100",
                    "quantity": 6,
                    "release_at": "2026-11-01T06:00:00Z",
                    "due_at": "2026-11-07T06:00:00Z",
                }
            ]
        }
        response = client.post(
            "/api/work-orders/import",
            files={
                "file": (
                    "orders.json",
                    io.BytesIO(json.dumps(payload).encode()),
                    "application/json",
                )
            },
            headers=auth,
        )
        assert response.status_code == 200, response.text
        assert response.json()["created"] == 1

    def test_import_rejects_a_missing_column(self, client: TestClient, auth: dict) -> None:
        payload = "code,quantity\nWO-1,10\n"
        response = client.post(
            "/api/work-orders/import",
            files={"file": ("orders.csv", io.BytesIO(payload.encode()), "text/csv")},
            headers=auth,
        )
        assert response.status_code == 400
        assert "missing required columns" in response.json()["detail"]


class TestSchedulingApi:
    def test_run_fcfs(self, client: TestClient, auth: dict) -> None:
        response = client.post("/api/scheduling/run", json={"engine": "fcfs"}, headers=auth)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] in {"FEASIBLE", "OPTIMAL"}
        assert body["validation"]["valid"] is True
        assert body["schedule"]["id"]

    @pytest.mark.slow
    def test_run_cpsat_and_compare(self, client: TestClient, auth: dict) -> None:
        response = client.post(
            "/api/scheduling/run",
            json={"engine": "cpsat", "time_limit_seconds": 10, "workers": 2, "random_seed": 42},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["solver_status"] in {"FEASIBLE", "OPTIMAL"}
        assert body["validation"]["valid"] is True
        assert body["comparison"] is not None
        assert body["comparison"]["rows"]

    def test_run_rejects_an_unknown_engine(self, client: TestClient, auth: dict) -> None:
        response = client.post("/api/scheduling/run", json={"engine": "magic"}, headers=auth)
        assert response.status_code == 422

    def test_run_rejects_an_out_of_range_time_limit(self, client: TestClient, auth: dict) -> None:
        response = client.post(
            "/api/scheduling/run", json={"engine": "fcfs", "time_limit_seconds": 0}, headers=auth
        )
        assert response.status_code == 422

    def test_list_and_inspect_schedules(self, client: TestClient, auth: dict) -> None:
        run = client.post("/api/scheduling/run", json={"engine": "fcfs"}, headers=auth)
        schedule_id = run.json()["schedule"]["id"]
        listing = client.get("/api/schedules", headers=auth)
        assert listing.status_code == 200
        assert any(row["id"] == schedule_id for row in listing.json())
        detail = client.get(f"/api/schedules/{schedule_id}", headers=auth)
        assert detail.status_code == 200
        assert detail.json()["operations"]

    def test_gantt_payload(self, client: TestClient, auth: dict) -> None:
        run = client.post("/api/scheduling/run", json={"engine": "fcfs"}, headers=auth)
        schedule_id = run.json()["schedule"]["id"]
        response = client.get(f"/api/schedules/{schedule_id}/gantt", headers=auth)
        assert response.status_code == 200
        body = response.json()
        assert body["operations"]
        assert body["machines"]

    def test_export_csv(self, client: TestClient, auth: dict) -> None:
        run = client.post("/api/scheduling/run", json={"engine": "fcfs"}, headers=auth)
        schedule_id = run.json()["schedule"]["id"]
        response = client.get(f"/api/schedules/{schedule_id}/export.csv", headers=auth)
        assert response.status_code == 200
        assert "work_order" in response.text.lower()

    def test_publish_marks_the_plan(self, client: TestClient, auth: dict) -> None:
        run = client.post("/api/scheduling/run", json={"engine": "fcfs"}, headers=auth)
        schedule_id = run.json()["schedule"]["id"]
        published = client.post(f"/api/schedules/{schedule_id}/publish", headers=auth)
        assert published.status_code == 200
        assert published.json()["is_published"] is True

    def test_compare_two_schedules(self, client: TestClient, auth: dict) -> None:
        first = client.post("/api/scheduling/run", json={"engine": "fcfs"}, headers=auth)
        second = client.post("/api/scheduling/run", json={"engine": "fcfs"}, headers=auth)
        ids = [first.json()["schedule"]["id"], second.json()["schedule"]["id"]]
        response = client.post("/api/schedules/compare", json={"schedule_ids": ids}, headers=auth)
        assert response.status_code == 200
        assert response.json()["comparisons"][0]["rows"]

    def test_compare_requires_two_schedules(self, client: TestClient, auth: dict) -> None:
        response = client.post("/api/schedules/compare", json={"schedule_ids": [1]}, headers=auth)
        assert response.status_code == 400

    def test_objectives_catalogue(self, client: TestClient, auth: dict) -> None:
        response = client.get("/api/scheduling/objectives", headers=auth)
        assert response.status_code == 200
        body = response.json()
        assert body["presets"]
        assert "tardiness" in body["terms"]


class TestResourceManagement:
    def test_create_update_machine_and_add_downtime(self, client: TestClient, auth: dict) -> None:
        calendar = client.get("/api/calendars", headers=auth).json()[0]
        created = client.post(
            "/api/machines",
            json={
                "code": "TEST-CNC-01",
                "name": "Test CNC",
                "machine_type": "CNC",
                "supported_families": ["A", "B"],
                "capabilities": ["cnc"],
                "calendar_id": calendar["id"],
                "hourly_cost": 75,
            },
            headers=auth,
        )
        assert created.status_code == 201, created.text
        machine_id = created.json()["id"]
        updated = client.patch(
            f"/api/machines/{machine_id}",
            json={"hourly_cost": 88, "is_active": False},
            headers=auth,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["hourly_cost"] == 88
        assert updated.json()["is_active"] is False

        downtime = client.post(
            f"/api/machines/{machine_id}/downtimes",
            json={
                "starts_at": "2026-12-01T08:00:00Z",
                "ends_at": "2026-12-01T12:00:00Z",
                "kind": "maintenance",
                "reason": "Scheduled test service",
            },
            headers=auth,
        )
        assert downtime.status_code == 201, downtime.text
        assert downtime.json()["reason"] == "Scheduled test service"
        listed = client.get(f"/api/machines/{machine_id}/downtimes", headers=auth)
        assert listed.status_code == 200
        assert len(listed.json()) == 1

    def test_downtime_must_have_a_positive_window(self, client: TestClient, auth: dict) -> None:
        machine = client.get("/api/machines", headers=auth).json()[0]
        response = client.post(
            f"/api/machines/{machine['id']}/downtimes",
            json={"starts_at": "2026-12-01T12:00:00Z", "ends_at": "2026-12-01T08:00:00Z"},
            headers=auth,
        )
        assert response.status_code == 422

    def test_create_update_operator_and_add_absence(self, client: TestClient, auth: dict) -> None:
        calendar = client.get("/api/calendars", headers=auth).json()[0]
        created = client.post(
            "/api/operators",
            json={
                "code": "TEST-OP-01",
                "name": "Test Operator",
                "skills": ["cnc-op"],
                "eligible_machine_types": ["CNC"],
                "calendar_id": calendar["id"],
            },
            headers=auth,
        )
        assert created.status_code == 201, created.text
        operator_id = created.json()["id"]
        updated = client.patch(
            f"/api/operators/{operator_id}", json={"weekly_regular_minutes": 2100}, headers=auth
        )
        assert updated.status_code == 200
        assert updated.json()["weekly_regular_minutes"] == 2100

        absence = client.post(
            f"/api/operators/{operator_id}/absences",
            json={
                "starts_at": "2026-12-02T08:00:00Z",
                "ends_at": "2026-12-02T16:00:00Z",
                "reason": "Training",
            },
            headers=auth,
        )
        assert absence.status_code == 201, absence.text
        listing = client.get(f"/api/operators/{operator_id}/absences", headers=auth)
        assert len(listing.json()) == 1
        assert listing.json()[0]["reason"] == "Training"

    def test_create_update_material_and_add_receipt(self, client: TestClient, auth: dict) -> None:
        created = client.post(
            "/api/materials",
            json={"sku": "TEST-RM-01", "name": "Test stock", "unit": "kg", "on_hand": 12},
            headers=auth,
        )
        assert created.status_code == 201, created.text
        material_id = created.json()["id"]
        updated = client.patch(
            f"/api/materials/{material_id}", json={"on_hand": 18, "is_active": False}, headers=auth
        )
        assert updated.status_code == 200
        assert updated.json()["on_hand"] == 18
        assert updated.json()["is_active"] is False
        receipt = client.post(
            f"/api/materials/{material_id}/receipts",
            json={"expected_at": "2026-12-03T08:00:00Z", "quantity": 25, "reference": "PO-TEST"},
            headers=auth,
        )
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["quantity"] == 25

    def test_calendar_exception_is_editable_from_the_api(
        self, client: TestClient, auth: dict
    ) -> None:
        calendar = next(
            row
            for row in client.get("/api/calendars", headers=auth).json()
            if not row["is_continuous"]
        )
        response = client.post(
            f"/api/calendars/{calendar['id']}/exceptions",
            json={"day": "2026-12-24", "kind": "shutdown", "label": "Plant closure"},
            headers=auth,
        )
        assert response.status_code == 201, response.text
        updated = client.get("/api/calendars", headers=auth).json()
        record = next(row for row in updated if row["id"] == calendar["id"])
        assert any(item["day"] == "2026-12-24" for item in record["exceptions"])

    def test_viewer_cannot_modify_resources(self, client: TestClient, viewer_token: str) -> None:
        calendars = client.get(
            "/api/calendars", headers={"Authorization": f"Bearer {viewer_token}"}
        ).json()
        response = client.post(
            "/api/machines",
            json={
                "code": "DENIED-01",
                "name": "Denied",
                "machine_type": "CNC",
                "calendar_id": calendars[0]["id"],
            },
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert response.status_code == 403


class TestDashboardAndResources:
    def test_dashboard(self, client: TestClient, auth: dict) -> None:
        response = client.get("/api/dashboard", headers=auth)
        assert response.status_code == 200
        body = response.json()
        assert body["work_orders"]["total"] == 20
        assert body["resources"]["machines"] == 11
        assert body["resources"]["operators"] == 10

    def test_machines(self, client: TestClient, auth: dict) -> None:
        response = client.get("/api/machines", headers=auth)
        assert response.status_code == 200
        assert len(response.json()) == 11

    def test_operators(self, client: TestClient, auth: dict) -> None:
        response = client.get("/api/operators", headers=auth)
        assert response.status_code == 200
        assert len(response.json()) == 10

    def test_materials_include_receipts(self, client: TestClient, auth: dict) -> None:
        response = client.get("/api/materials", headers=auth)
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 4
        assert body[0]["total_available"] >= body[0]["on_hand"]

    def test_routing_operation_batch_and_alternative_fields_are_editable(
        self, client: TestClient, auth: dict
    ) -> None:
        routings = client.get("/api/routings", headers=auth).json()
        operation = routings[0]["operations"][0]
        machines = client.get("/api/machines", headers=auth).json()
        machine_ids = [m["id"] for m in machines if m["machine_type"] == operation["machine_type"]]
        response = client.patch(
            f"/api/routings/operations/{operation['id']}",
            json={
                "batch_size": 20,
                "batch_setup_minutes": 5,
                "transfer_time_minutes": 12,
                "preferred_machine_ids": machine_ids[:2],
                "allow_alternative_machines": True,
                "is_parallel_group": "fixture-A",
            },
            headers=auth,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["batch_size"] == 20
        assert body["batch_setup_minutes"] == 5
        assert body["transfer_time_minutes"] == 12
        assert body["preferred_machine_ids"] == machine_ids[:2]
        assert body["is_parallel_group"] == "fixture-A"

    def test_calendars(self, client: TestClient, auth: dict) -> None:
        response = client.get("/api/calendars", headers=auth)
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        codes = {row["code"] for row in body}
        assert "PLANT-STD" in codes

    def test_audit_log_is_recorded(self, client: TestClient, auth: dict) -> None:
        client.post("/api/scheduling/run", json={"engine": "fcfs"}, headers=auth)
        response = client.get("/api/audit", headers=auth)
        assert response.status_code == 200
        assert any(row["action"].startswith("schedule") for row in response.json())


class TestJobs:
    def test_job_lifecycle(self, client: TestClient, auth: dict) -> None:
        response = client.post(
            "/api/jobs",
            json={"kind": "benchmark", "payload": {"scale": "small"}},
            headers=auth,
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        listing = client.get("/api/jobs", headers=auth)
        assert listing.status_code == 200
        assert any(row["id"] == job_id for row in listing.json())
        detail = client.get(f"/api/jobs/{job_id}", headers=auth)
        assert detail.status_code == 200
        assert detail.json()["status"] in {"queued", "running", "succeeded", "failed", "cancelled"}
