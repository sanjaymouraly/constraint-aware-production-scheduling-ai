"""Command line utilities: database setup, demo data, benchmarks.

Usage::

    python -m app.cli init-db          # create tables (SQLite) or run migrations
    python -m app.cli seed --scale medium
    python -m app.cli reset --scale medium
    python -m app.cli benchmark --scale medium --time-limit 20
    python -m app.cli create-user --username planner --password secret
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.config import BACKEND_DIR, planning_now, settings
from app.db import SessionLocal
from app.logging_config import configure_logging

configure_logging()

#: Fixed planning instant used by the reproducible benchmarks.  It is a Monday
#: so that the plant calendar (Monday-Friday, two shifts) and the Sunday
#: plant shutdown of the demo factory line up exactly as documented.
BENCHMARK_REFERENCE = datetime(2024, 5, 13, 0, 0, tzinfo=UTC)


def _reference(value: str | None) -> datetime:
    """Parse an optional ``--reference`` argument into an aware instant."""
    if not value:
        return planning_now()
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _configure_console() -> None:
    """Make the CLI usable in a Windows console.

    The default Windows code page (cp1252) cannot encode the arrow and the
    other typographic characters the CLI prints, which would abort the command
    with a ``UnicodeEncodeError``.  Re-configuring the streams to UTF-8 keeps
    the output readable instead.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


def _init_db() -> None:
    """Apply the Alembic migration chain to the configured database."""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(alembic_cfg, "head")
    print(f"Database migrated: {settings.database_url}")


def _seed(scale: str, seed: int, clear: bool, now: datetime | None = None) -> dict:
    from app.seed.demo_factory import seed_demo_data

    _init_db()
    with SessionLocal() as session:
        counts = seed_demo_data(session, scale=scale, seed=seed, clear=clear, now=now)
        session.commit()
    return counts


def _reset(scale: str, seed: int, now: datetime | None = None) -> None:
    from app.seed.demo_factory import seed_demo_data
    from app.services.auth import ensure_bootstrap_admin

    _init_db()
    with SessionLocal() as session:
        counts = seed_demo_data(session, scale=scale, seed=seed, clear=True, now=now)
        ensure_bootstrap_admin(session)
        session.commit()
    print("Demo factory created:")
    for key, value in counts.items():
        print(f"  {key:16s} {value}")
    print(
        "\nSign in with the bootstrap administrator: "
        f"{settings.bootstrap_admin_username} / {settings.bootstrap_admin_password}"
    )


def _benchmark(
    scale: str,
    time_limit: float,
    output: str | None,
    now: datetime,
    workers: int | None = None,
    deterministic_time: float | None = None,
) -> None:
    from app.scheduling.builder import BuildOptions, build_instance
    from app.scheduling.cpsat import CpSatOptions, CpSatScheduler
    from app.scheduling.fcfs import run_fcfs
    from app.scheduling.metrics import compare_metrics, compute_metrics
    from app.scheduling.postprocess import apply_setup_gaps
    from app.scheduling.validator import validate_schedule
    from app.seed.demo_factory import seed_demo_data

    _init_db()
    with SessionLocal() as session:
        seed_demo_data(session, scale=scale, clear=True, now=now)
        session.commit()
        instance = build_instance(session, BuildOptions(reference_now=now))
        print(
            f"Instance: {len(instance.tasks)} operations, {len(instance.machines)} machines, "
            f"{len(instance.operators)} operators, horizon "
            f"{instance.horizon_start:%Y-%m-%d %H:%M} → {instance.horizon_end:%Y-%m-%d %H:%M}"
        )
        print(f"Planning reference: {now.isoformat()}")

        baseline = run_fcfs(instance)
        apply_setup_gaps(instance, baseline.assignments)
        baseline_report = validate_schedule(instance, baseline.assignments)
        baseline_metrics = compute_metrics(
            instance, baseline.assignments, solver_runtime_ms=baseline.runtime_ms
        )

        scheduler = CpSatScheduler(
            instance,
            CpSatOptions(
                time_limit_seconds=time_limit,
                workers=workers or settings.solver_workers,
                deterministic_time_limit=deterministic_time,
            ),
        )
        scheduler.build()
        scheduler.add_hint(baseline.assignments)
        optimised = scheduler.solve()
        apply_setup_gaps(instance, optimised.assignments)
        optimised_report = validate_schedule(instance, optimised.assignments)
        optimised_metrics = compute_metrics(
            instance, optimised.assignments, solver_runtime_ms=optimised.runtime_ms
        )

    comparison = compare_metrics(baseline_metrics, optimised_metrics)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scale": scale,
        "time_limit_seconds": time_limit,
        "deterministic_time_limit": deterministic_time,
        "workers": workers or settings.solver_workers,
        "planning_reference": now.isoformat(),
        "instance": {
            "operations": len(instance.tasks),
            "machines": len(instance.machines),
            "operators": len(instance.operators),
            "horizon_start": instance.horizon_start.isoformat(),
            "horizon_end": instance.horizon_end.isoformat(),
        },
        "baseline": {
            "engine": "fcfs",
            "status": baseline.status,
            "valid": baseline_report.valid,
            "runtime_ms": round(baseline.runtime_ms, 1),
            "metrics": baseline_metrics,
        },
        "optimised": {
            "engine": "cpsat",
            "status": optimised.status,
            "valid": optimised_report.valid,
            "objective_value": optimised.objective_value,
            "best_bound": optimised.best_bound,
            "runtime_ms": round(optimised.runtime_ms, 1),
            "metrics": optimised_metrics,
        },
        "comparison": comparison,
    }
    print()
    print(f"{'metric':30s} {'FCFS':>14s} {'CP-SAT':>14s} {'delta':>12s}  verdict")
    for row in comparison["rows"]:
        print(
            f"{row['metric']:30s} {row['baseline']:>14} {row['optimised']:>14} "
            f"{row['delta']:>12}  {row['verdict']}"
        )
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str))
        print(f"\nMachine readable result written to {path}")


def _create_user(username: str, password: str, role: str, email: str) -> None:
    from app.models import User
    from app.services.auth import hash_password

    with SessionLocal() as session:
        existing = session.query(User).filter_by(username=username).first()
        if existing:
            print(f"User '{username}' already exists.")
            return
        session.add(
            User(
                username=username,
                email=email,
                hashed_password=hash_password(password),
                role=role,
                is_active=True,
            )
        )
        session.commit()
        print(f"User '{username}' created with role '{role}'.")


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create the database schema")

    seed_parser = sub.add_parser("seed", help="Load the demonstration factory")
    seed_parser.add_argument("--scale", default="medium", choices=["small", "medium", "large"])
    seed_parser.add_argument("--seed", type=int, default=20240517)
    seed_parser.add_argument("--keep", action="store_true", help="Do not clear existing data")
    seed_parser.add_argument(
        "--reference",
        default=None,
        help="ISO-8601 instant used as 'now' (default: the current time)",
    )

    reset_parser = sub.add_parser("reset", help="Recreate the demo factory from scratch")
    reset_parser.add_argument("--scale", default="medium", choices=["small", "medium", "large"])
    reset_parser.add_argument("--seed", type=int, default=20240517)
    reset_parser.add_argument(
        "--reference",
        default=None,
        help="ISO-8601 instant used as 'now' (default: the current time)",
    )

    bench_parser = sub.add_parser("benchmark", help="Run the FCFS vs CP-SAT benchmark")
    bench_parser.add_argument("--scale", default="medium", choices=["small", "medium", "large"])
    bench_parser.add_argument("--time-limit", type=float, default=20.0)
    bench_parser.add_argument("--output", default=None)
    bench_parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "CP-SAT search workers. Use 1 for a deterministic run (the search is "
            "then reproducible), more workers for a faster but "
            "time-dependent result."
        ),
    )
    bench_parser.add_argument(
        "--deterministic-time",
        type=float,
        default=None,
        help=(
            "Deterministic search budget instead of the wall-clock limit. The "
            "solver then performs the same search on every machine, so the "
            "result is reproducible independently of the host's speed."
        ),
    )
    bench_parser.add_argument(
        "--reference",
        default=None,
        help=(
            "ISO-8601 instant used as 'now'. Defaults to the fixed benchmark "
            f"reference {BENCHMARK_REFERENCE.isoformat()} so the published "
            "results are reproducible."
        ),
    )

    user_parser = sub.add_parser("create-user", help="Create a platform user")
    user_parser.add_argument("--username", required=True)
    user_parser.add_argument("--password", required=True)
    user_parser.add_argument("--role", default="planner", choices=["admin", "planner", "viewer"])
    user_parser.add_argument("--email", default=None)

    args = parser.parse_args(argv)

    if args.command == "init-db":
        _init_db()
    elif args.command == "seed":
        counts = _seed(args.scale, args.seed, not args.keep, _reference(args.reference))
        print(json.dumps(counts, indent=2))
    elif args.command == "reset":
        _reset(args.scale, args.seed, _reference(args.reference))
    elif args.command == "benchmark":
        reference = _reference(args.reference) if args.reference else BENCHMARK_REFERENCE
        _benchmark(
            args.scale,
            args.time_limit,
            args.output,
            reference,
            args.workers,
            args.deterministic_time,
        )
    elif args.command == "create-user":
        _create_user(
            args.username,
            args.password,
            args.role,
            args.email or f"{args.username}@example.com",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
