"""A clean database reaches the current Alembic head before the app starts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_cli_init_db_runs_the_complete_migration_chain(tmp_path: Path) -> None:
    database = tmp_path / "migration-test.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "SECRET_KEY": "migration-test-secret-long-enough-for-tests",
        "ENVIRONMENT": "test",
    }
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "init-db"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Database migrated" in result.stdout

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "alembic_version" in tables
        assert "routing_operations" in tables
        columns = {column["name"] for column in inspector.get_columns("routing_operations")}
        assert {"batch_size", "batch_setup_minutes"}.issubset(columns)
        revisions = (
            engine.connect().exec_driver_sql("SELECT version_num FROM alembic_version").all()
        )
        assert revisions == [("00914bc60b0a",)]
    finally:
        engine.dispose()
