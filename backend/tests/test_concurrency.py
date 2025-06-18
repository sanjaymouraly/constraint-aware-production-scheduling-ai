"""Concurrency, job lifecycle and recovery tests."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


class TestJobLifecycle:
    def test_job_runs_in_the_background_and_succeeds(self, client: TestClient, auth: dict) -> None:
        response = client.post(
            "/api/jobs",
            json={"engine": "fcfs", "time_limit_seconds": 10},
            headers=auth,
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        deadline = time.time() + 60
        status = None
        while time.time() < deadline:
            detail = client.get(f"/api/jobs/{job_id}", headers=auth).json()
            status = detail["status"]
            if status in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.25)

        assert status == "succeeded"
        detail = client.get(f"/api/jobs/{job_id}", headers=auth).json()
        assert detail["result"]["schedule_id"]
        assert detail["result"]["validation"]["valid"] is True
        assert detail["finished_at"]

    def test_job_can_be_cancelled(self, client: TestClient, auth: dict, session: Session) -> None:
        response = client.post(
            "/api/jobs",
            json={"engine": "cpsat", "time_limit_seconds": 120},
            headers=auth,
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        # The job may still be queued — cancel it either way.
        cancel = client.post(f"/api/jobs/{job_id}/cancel", headers=auth)
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"

        deadline = time.time() + 60
        while time.time() < deadline:
            detail = client.get(f"/api/jobs/{job_id}", headers=auth).json()
            if detail["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.25)
        assert detail["status"] in {"cancelled", "succeeded"}

    def test_cancelling_a_finished_job_is_rejected(self, client: TestClient, auth: dict) -> None:
        response = client.post("/api/jobs", json={"engine": "fcfs"}, headers=auth)
        job_id = response.json()["job_id"]
        deadline = time.time() + 60
        while time.time() < deadline:
            detail = client.get(f"/api/jobs/{job_id}", headers=auth).json()
            if detail["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.2)
        assert client.post(f"/api/jobs/{job_id}/cancel", headers=auth).status_code == 409

    def test_unknown_job_returns_404(self, client: TestClient, auth: dict) -> None:
        assert client.get("/api/jobs/999999", headers=auth).status_code == 404

    def test_viewer_cannot_queue_a_job(self, client: TestClient, viewer_token: str) -> None:
        response = client.post(
            "/api/jobs",
            json={"engine": "fcfs"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert response.status_code == 403


class TestConcurrentOptimisation:
    @pytest.mark.slow
    def test_two_parallel_runs_both_produce_valid_plans(
        self, client: TestClient, auth: dict
    ) -> None:
        """Concurrent optimisation must not corrupt the persisted state."""
        results: list[int] = []
        rejected: list[int] = []
        errors: list[str] = []

        def _run() -> None:
            try:
                response = client.post(
                    "/api/scheduling/run",
                    json={"engine": "fcfs", "name": "parallel"},
                    headers=auth,
                )
                if response.status_code == 200:
                    results.append(response.json()["schedule"]["id"])
                elif response.status_code == 429:
                    # The concurrency guard refuses extra optimisations.
                    rejected.append(429)
                else:
                    errors.append(f"{response.status_code}: {response.text[:200]}")
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(str(exc))

        threads = [threading.Thread(target=_run) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=180)

        assert not errors, errors
        assert results, "at least one run must succeed"
        assert len(set(results)) == len(results)
        assert len(results) + len(rejected) == 3

        for schedule_id in results:
            detail = client.get(f"/api/schedules/{schedule_id}", headers=auth).json()
            assert detail["validation"]["valid"] is True
            assert detail["operations"]

    def test_concurrent_optimisation_limit_is_enforced(self, session: Session) -> None:
        """The platform refuses to start more parallel optimisations than allowed."""
        from app.config import settings
        from app.services.scheduling_service import ConcurrencyGuard

        guard = ConcurrencyGuard(limit=settings.max_concurrent_optimizations)
        tokens = []
        for _ in range(settings.max_concurrent_optimizations):
            token = guard.acquire("a")
            assert token is not None
            tokens.append(token)
        assert guard.acquire("b") is None
        guard.release(tokens[0])
        assert guard.acquire("b") is not None
        for token in tokens[1:]:
            guard.release(token)


class TestRecovery:
    def test_a_failed_run_does_not_persist_a_schedule(self, client: TestClient, auth: dict) -> None:
        before = len(client.get("/api/schedules", headers=auth).json())
        response = client.post(
            "/api/scheduling/run",
            json={"engine": "cpsat", "horizon_start": "1990-01-01T00:00:00Z"},
            headers=auth,
        )
        # A horizon in the past cannot contain any work order.
        assert response.status_code in {200, 400}
        if response.status_code == 200:
            body = response.json()
            if body["schedule"] is None:
                assert len(client.get("/api/schedules", headers=auth).json()) == before

    def test_audit_log_is_immutable_through_the_api(self, client: TestClient, auth: dict) -> None:
        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)("/api/audit", headers=auth)
            assert response.status_code == 405, method

    def test_health_endpoint_never_touches_the_database(self, client: TestClient) -> None:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert "database" not in response.json()
