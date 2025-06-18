"""Tests for the scenario (what-if) subsystem.

A scenario clones an existing plan, applies disruptions to the master data,
re-plans and compares the result with the plan it was cloned from.  The live
schedule must never change unless the scenario is published explicitly.
"""

from __future__ import annotations

import pytest
from app.models import Machine, MachineDowntime, OperationalEvent, WorkOrder
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture()
def base_schedule(client: TestClient, auth: dict) -> int:
    response = client.post("/api/scheduling/run", json={"engine": "fcfs"}, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()["schedule"]["id"]


class TestScenarioLifecycle:
    def test_create_scenario_clones_the_plan(
        self, client: TestClient, auth: dict, base_schedule: int
    ) -> None:
        response = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "Breakdown test"},
            headers=auth,
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "draft"
        assert body["base_schedule_id"] == base_schedule
        assert body["assumptions"]["clone_schedule_id"]
        assert body["assumptions"]["locked_operations"] >= 0

    def test_create_scenario_requires_an_existing_plan(
        self, client: TestClient, auth: dict
    ) -> None:
        response = client.post(
            "/api/scenarios",
            json={"base_schedule_id": 999999, "name": "Nope"},
            headers=auth,
        )
        assert response.status_code == 400

    def test_scenario_does_not_touch_the_live_plan(
        self, client: TestClient, auth: dict, base_schedule: int
    ) -> None:
        before = client.get(f"/api/schedules/{base_schedule}", headers=auth).json()
        scenario = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "No side effects"},
            headers=auth,
        ).json()
        client.post(
            f"/api/scenarios/{scenario['id']}/disruptions",
            json={
                "kind": "machine_breakdown",
                "title": "CNC-01 fails",
                "severity": "critical",
                "payload": {"machine_id": 1, "duration_hours": 6},
            },
            headers=auth,
        )
        after = client.get(f"/api/schedules/{base_schedule}", headers=auth).json()
        assert after["is_published"] == before["is_published"]
        assert len(after["operations"]) == len(before["operations"])


class TestDisruptions:
    def test_machine_breakdown_creates_downtime(
        self,
        client: TestClient,
        auth: dict,
        base_schedule: int,
        session: Session,
    ) -> None:
        machine = session.query(Machine).filter(Machine.code == "CNC-01").one()
        scenario = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "Breakdown"},
            headers=auth,
        ).json()
        response = client.post(
            f"/api/scenarios/{scenario['id']}/disruptions",
            json={
                "kind": "machine_breakdown",
                "title": "CNC-01 fails",
                "severity": "critical",
                "payload": {"machine_id": machine.id, "duration_hours": 6},
            },
            headers=auth,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["kind"] == "machine_breakdown"
        assert body["payload"]["machine_code"] == "CNC-01"
        session.expire_all()
        assert session.query(MachineDowntime).filter_by(machine_id=machine.id).count() >= 1

    def test_missing_payload_key_is_a_client_error(
        self, client: TestClient, auth: dict, base_schedule: int
    ) -> None:
        scenario = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "Bad payload"},
            headers=auth,
        ).json()
        response = client.post(
            f"/api/scenarios/{scenario['id']}/disruptions",
            json={"kind": "machine_breakdown", "payload": {}},
            headers=auth,
        )
        assert response.status_code == 400
        assert "machine_id" in response.json()["detail"]

    def test_material_delay_moves_the_receipt(
        self, client: TestClient, auth: dict, base_schedule: int, session: Session
    ) -> None:
        from app.models import MaterialReceipt

        receipt = session.query(MaterialReceipt).order_by(MaterialReceipt.id).first()
        original = receipt.expected_at
        scenario = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "Late delivery"},
            headers=auth,
        ).json()
        response = client.post(
            f"/api/scenarios/{scenario['id']}/disruptions",
            json={
                "kind": "material_delay",
                "title": "Supplier delay",
                "payload": {"receipt_id": receipt.id, "delay_days": 3},
            },
            headers=auth,
        )
        assert response.status_code == 200, response.text
        session.expire_all()
        updated = session.get(MaterialReceipt, receipt.id)
        assert updated.expected_at > original

    def test_urgent_order_creates_a_work_order(
        self, client: TestClient, auth: dict, base_schedule: int, session: Session
    ) -> None:
        template = session.query(WorkOrder).order_by(WorkOrder.id).first()
        before = session.query(WorkOrder).count()
        scenario = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "Urgent order"},
            headers=auth,
        ).json()
        response = client.post(
            f"/api/scenarios/{scenario['id']}/disruptions",
            json={
                "kind": "urgent_order",
                "title": "Rush job",
                "payload": {"template_work_order_id": template.id, "quantity": 5},
            },
            headers=auth,
        )
        assert response.status_code == 200, response.text
        session.expire_all()
        assert session.query(WorkOrder).count() == before + 1
        new_order = (
            session.query(WorkOrder).filter_by(is_urgent=True).order_by(WorkOrder.id.desc()).first()
        )
        assert new_order.priority == 5

    def test_order_cancellation(self, client: TestClient, auth: dict, base_schedule: int) -> None:
        orders = client.get("/api/work-orders", headers=auth).json()
        target = orders[0]
        scenario = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "Cancellation"},
            headers=auth,
        ).json()
        response = client.post(
            f"/api/scenarios/{scenario['id']}/disruptions",
            json={
                "kind": "order_cancelled",
                "payload": {"work_order_id": target["id"]},
            },
            headers=auth,
        )
        assert response.status_code == 200, response.text
        after = client.get(f"/api/work-orders/{target['id']}", headers=auth).json()
        assert after["status"] == "cancelled"

    def test_quantity_change(self, client: TestClient, auth: dict, base_schedule: int) -> None:
        orders = client.get("/api/work-orders", headers=auth).json()
        target = orders[1]
        scenario = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "More volume"},
            headers=auth,
        ).json()
        response = client.post(
            f"/api/scenarios/{scenario['id']}/disruptions",
            json={
                "kind": "quantity_change",
                "payload": {"work_order_id": target["id"], "quantity": 99},
            },
            headers=auth,
        )
        assert response.status_code == 200, response.text
        after = client.get(f"/api/work-orders/{target['id']}", headers=auth).json()
        assert after["quantity"] == 99

    def test_operator_absence(self, client: TestClient, auth: dict, base_schedule: int) -> None:
        operator = session_operator_id()
        scenario = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "Sick leave"},
            headers=auth,
        ).json()
        response = client.post(
            f"/api/scenarios/{scenario['id']}/disruptions",
            json={
                "kind": "operator_absence",
                "payload": {"operator_id": operator, "duration_hours": 8},
            },
            headers=auth,
        )
        assert response.status_code == 200, response.text
        assert response.json()["payload"]["operator_code"]

    def test_events_are_recorded(
        self, client: TestClient, auth: dict, base_schedule: int, session: Session
    ) -> None:
        scenario = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "Event log"},
            headers=auth,
        ).json()
        client.post(
            f"/api/scenarios/{scenario['id']}/disruptions",
            json={
                "kind": "machine_breakdown",
                "payload": {"machine_id": 1, "duration_hours": 2},
            },
            headers=auth,
        )
        session.expire_all()
        events = session.query(OperationalEvent).filter_by(kind="machine_breakdown").all()
        assert events
        assert events[-1].payload["machine_code"]


def session_operator_id() -> int:
    from app.db import SessionLocal
    from app.models import Operator

    with SessionLocal() as db:
        return db.query(Operator).order_by(Operator.id).first().id


class TestSimulation:
    @pytest.mark.slow
    def test_simulate_returns_a_validated_plan(
        self, client: TestClient, auth: dict, base_schedule: int
    ) -> None:
        scenario = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "Re-plan"},
            headers=auth,
        ).json()
        response = client.post(
            f"/api/scenarios/{scenario['id']}/simulate",
            json={"engine": "fcfs", "time_limit_seconds": 10},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["schedule_id"]
        assert body["validation"]["valid"] is True
        assert body["impact"]["available"] is True
        assert body["impact"]["metrics_before"] is not None
        assert body["impact"]["metrics_after"] is not None

    @pytest.mark.slow
    def test_publishing_a_scenario_makes_it_the_live_plan(
        self, client: TestClient, auth: dict, base_schedule: int
    ) -> None:
        scenario = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "Publish me"},
            headers=auth,
        ).json()
        client.post(
            f"/api/scenarios/{scenario['id']}/simulate",
            json={"engine": "fcfs"},
            headers=auth,
        )
        response = client.post(f"/api/scenarios/{scenario['id']}/publish", headers=auth)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_published"] is True
        schedules = client.get("/api/schedules", headers=auth).json()
        published = [row for row in schedules if row["is_published"]]
        assert len(published) == 1
        assert published[0]["id"] == body["id"]

    def test_publishing_without_simulating_is_rejected(
        self, client: TestClient, auth: dict, base_schedule: int
    ) -> None:
        scenario = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "Not simulated"},
            headers=auth,
        ).json()
        response = client.post(f"/api/scenarios/{scenario['id']}/publish", headers=auth)
        assert response.status_code == 409

    def test_impact_is_unavailable_before_the_first_simulation(
        self, client: TestClient, auth: dict, base_schedule: int
    ) -> None:
        scenario = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "No impact yet"},
            headers=auth,
        ).json()
        response = client.get(f"/api/scenarios/{scenario['id']}/impact", headers=auth)
        assert response.status_code == 200
        assert response.json()["available"] is False


class TestScenarioApiGuards:
    def test_viewer_cannot_create_a_scenario(
        self, client: TestClient, viewer_token: str, base_schedule: int
    ) -> None:
        response = client.post(
            "/api/scenarios",
            json={"base_schedule_id": base_schedule, "name": "Nope"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert response.status_code == 403

    def test_scenario_listing_is_available_to_viewers(
        self, client: TestClient, viewer_token: str
    ) -> None:
        response = client.get("/api/scenarios", headers={"Authorization": f"Bearer {viewer_token}"})
        assert response.status_code == 200

    def test_unknown_scenario_returns_404(self, client: TestClient, auth: dict) -> None:
        assert client.get("/api/scenarios/999999", headers=auth).status_code == 404
