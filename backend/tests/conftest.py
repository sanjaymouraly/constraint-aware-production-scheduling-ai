"""Shared pytest fixtures.

Every test runs against a throw-away SQLite database.  The environment
variable is set **before** ``app.db`` is imported so that the module level
engine always points at the test database.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

_TMP_DIR = tempfile.mkdtemp(prefix="ips-tests-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_TMP_DIR) / 'test.db'}")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Schedule, ScheduleOperation
from app.scheduling.builder import BuildOptions, build_instance
from app.seed.demo_factory import seed_demo_data
from app.services.auth import ensure_bootstrap_admin
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

#: Fixed planning reference for the whole test suite.
#:
#: The demo factory and the planning horizon are relative to "now", so without
#: a fixed instant the *instance* would change with the calendar day and every
#: assertion about plan quality would be flaky.  The instant is a Monday, so
#: the plant calendar (Monday-Friday, two shifts) and the Sunday plant
#: shutdown of the demo factory line up exactly as documented.
PLANNING_REFERENCE = datetime(2024, 5, 13, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    # The schema is created once for the whole session; the temporary database
    # file is removed by the operating system when the sandbox is recycled.
    Base.metadata.create_all(engine)
    yield


@pytest.fixture()
def session() -> Iterator[Session]:
    """A clean database seeded with the deterministic medium demo factory."""
    with SessionLocal() as db:
        seed_demo_data(db, scale="medium", clear=True, now=PLANNING_REFERENCE)
        db.commit()
        yield db
        db.rollback()


@pytest.fixture()
def empty_session() -> Iterator[Session]:
    """A clean database without any demo data."""
    with SessionLocal() as db:
        seed_demo_data(db, scale="small", clear=True, now=PLANNING_REFERENCE)
        for row in db.query(ScheduleOperation).all():
            db.delete(row)
        for row in db.query(Schedule).all():
            db.delete(row)
        db.commit()
        yield db
        db.rollback()


@pytest.fixture()
def client(session: Session) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def admin_token(client: TestClient) -> str:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture()
def auth(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


def _ensure_user(
    client: TestClient, username: str, email: str, password: str, full_name: str, role: str
) -> None:
    """Create a test user, replacing any leftover from a previous test."""
    admin_headers = {"Authorization": f"Bearer {_admin_token(client)}"}
    existing = client.get("/api/auth/users", headers=admin_headers).json()
    for row in existing:
        if row["username"] == username:
            client.delete(f"/api/auth/users/{row['id']}", headers=admin_headers)
    response = client.post(
        "/api/auth/users",
        json={
            "username": username,
            "email": email,
            "password": password,
            "full_name": full_name,
            "role": role,
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text


@pytest.fixture()
def planner_token(client: TestClient) -> str:
    _ensure_user(
        client,
        username="planner1",
        email="planner@example.com",
        password="planner-pass",
        full_name="Plan Ivan",
        role="planner",
    )
    login = client.post(
        "/api/auth/login", json={"username": "planner1", "password": "planner-pass"}
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


@pytest.fixture()
def viewer_token(client: TestClient) -> str:
    _ensure_user(
        client,
        username="viewer1",
        email="viewer@example.com",
        password="viewer-pass",
        full_name="View Vera",
        role="viewer",
    )
    login = client.post("/api/auth/login", json={"username": "viewer1", "password": "viewer-pass"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _admin_token(client: TestClient) -> str:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture()
def instance(session: Session):
    """A medium scheduling instance built from the demo factory."""
    return build_instance(session, BuildOptions(reference_now=PLANNING_REFERENCE))


@pytest.fixture()
def bootstrap_admin(session: Session) -> None:
    ensure_bootstrap_admin(session)
    session.commit()
