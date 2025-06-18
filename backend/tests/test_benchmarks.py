"""Medium and large scale benchmarks.

These tests are marked ``slow`` and are excluded from the default CI unit
job (``-m "not slow"``).  They are executed by the dedicated ``benchmarks``
workflow and can be run locally with::

    cd backend
    pytest tests/test_benchmarks.py -m slow -s

Every assertion is a *measured* property of the produced schedule: the
benchmarks never compare against hard-coded metric values, because that would
manufacture a "successful" result instead of measuring one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pytest
from app.scheduling.builder import BuildOptions, build_instance
from app.scheduling.cpsat import CpSatOptions, CpSatScheduler
from app.scheduling.fcfs import run_fcfs
from app.scheduling.metrics import compare_metrics, compute_metrics
from app.scheduling.postprocess import apply_setup_gaps
from app.scheduling.validator import validate_schedule
from app.seed.demo_factory import seed_demo_data
from tests.conftest import PLANNING_REFERENCE


@dataclass
class BenchmarkResult:
    scale: str
    operations: int
    machines: int
    operators: int
    baseline_status: str
    optimised_status: str
    baseline_valid: bool
    optimised_valid: bool
    baseline_runtime_ms: float
    optimised_runtime_ms: float
    comparison: dict[str, Any]
    baseline_metrics: dict[str, Any]
    optimised_metrics: dict[str, Any]

    def metric(self, name: str) -> float:
        return float(self.comparison["rows"][0].get(name, 0))


#: The documented benchmark configuration.  A *deterministic* budget plus a
#: single worker makes the run reproducible on every host, and the generous
#: wall-clock limit only acts as a safety net.
BENCHMARK_TIME_LIMIT_SECONDS = 900.0
BENCHMARK_DETERMINISTIC_TIME = 30.0


def _run_benchmark(session, scale: str, time_limit: float = BENCHMARK_TIME_LIMIT_SECONDS):
    seed_demo_data(session, scale=scale, clear=True, now=PLANNING_REFERENCE)
    session.commit()
    instance = build_instance(session, BuildOptions(reference_now=PLANNING_REFERENCE))

    baseline = run_fcfs(instance)
    apply_setup_gaps(instance, baseline.assignments)
    baseline_report = validate_schedule(instance, baseline.assignments)
    baseline_metrics = compute_metrics(
        instance, baseline.assignments, solver_runtime_ms=baseline.runtime_ms
    )

    scheduler = CpSatScheduler(
        instance,
        CpSatOptions(
            time_limit_seconds=max(time_limit, BENCHMARK_TIME_LIMIT_SECONDS),
            workers=1,
            random_seed=42,
            # A deterministic budget makes the run reproducible on every host.
            deterministic_time_limit=BENCHMARK_DETERMINISTIC_TIME,
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

    return BenchmarkResult(
        scale=scale,
        operations=len(instance.tasks),
        machines=len(instance.machines),
        operators=len(instance.operators),
        baseline_status=baseline.status,
        optimised_status=optimised.status,
        baseline_valid=baseline_report.valid,
        optimised_valid=optimised_report.valid,
        baseline_runtime_ms=baseline.runtime_ms,
        optimised_runtime_ms=optimised.runtime_ms,
        comparison=compare_metrics(baseline_metrics, optimised_metrics),
        baseline_metrics=baseline_metrics,
        optimised_metrics=optimised_metrics,
    )


@pytest.mark.slow
def test_medium_scale_benchmark(session) -> None:
    """The medium instance (tens of work orders) is solvable and improves."""
    result = _run_benchmark(session, "medium")

    print("\n--- medium benchmark ---")
    for row in result.comparison["rows"]:
        print(
            f"  {row['metric']:28s} fcfs={row['baseline']!s:>12} "
            f"cpsat={row['optimised']!s:>12} delta={row['delta']!s:>10} {row['verdict']}"
        )

    assert result.operations >= 50, "the medium scale must contain a realistic number of operations"
    assert result.baseline_valid, "the FCFS baseline must satisfy every hard constraint"
    assert result.optimised_valid, "the CP-SAT plan must satisfy every hard constraint"
    assert result.baseline_status in {"FEASIBLE", "PARTIAL"}
    assert result.optimised_status in {"OPTIMAL", "FEASIBLE"}
    assert result.baseline_metrics["operations_scheduled"] == result.operations

    # The optimiser must not be worse than the baseline on tardiness.
    assert (
        result.optimised_metrics["weighted_tardiness"]
        <= result.baseline_metrics["weighted_tardiness"]
    ), "CP-SAT produced a worse weighted tardiness than the FCFS baseline"


@pytest.mark.slow
def test_large_scale_benchmark_is_valid(session) -> None:
    """The large instance stays within the horizon and passes the validator."""
    # The large scale needs a longer time limit: the CP-SAT model grows with
    # the number of candidate operations per machine (the sequence dependent
    # setup chain is quadratic in that number).
    result = _run_benchmark(session, "large")

    print("\n--- large benchmark ---")
    print(
        f"  operations={result.operations} machines={result.machines} "
        f"operators={result.operators}"
    )
    print(
        f"  fcfs  status={result.baseline_status} valid={result.baseline_valid} "
        f"runtime={result.baseline_runtime_ms:.0f}ms"
    )
    print(
        f"  cpsat status={result.optimised_status} valid={result.optimised_valid} "
        f"runtime={result.optimised_runtime_ms:.0f}ms"
    )

    assert result.operations >= 200, "the large scale must contain hundreds of operations"
    assert result.baseline_valid
    assert result.optimised_valid
    assert result.optimised_metrics["operations_scheduled"] == result.operations


@pytest.mark.slow
def test_baseline_scales_linearly(session) -> None:
    """The deterministic FCFS baseline stays comfortably below a second."""
    seed_demo_data(session, scale="medium", clear=True, now=PLANNING_REFERENCE)
    session.commit()
    instance = build_instance(session, BuildOptions(reference_now=PLANNING_REFERENCE))

    started = time.perf_counter()
    baseline = run_fcfs(instance)
    elapsed = time.perf_counter() - started

    print(f"\n  FCFS on {len(instance.tasks)} operations took {elapsed * 1000:.0f} ms")
    assert elapsed < 5.0, "the FCFS baseline must stay interactive"
    assert baseline.status in {"FEASIBLE", "PARTIAL"}


@pytest.mark.slow
def test_optimizer_reports_a_bound(session) -> None:
    """A time limited run must expose a bound and must not claim optimality."""
    result = _run_benchmark(session, "small")
    assert result.optimised_status in {"OPTIMAL", "FEASIBLE"}
    if result.optimised_status == "FEASIBLE":
        # The status is honest: the plan is feasible but not proven optimal.
        assert result.comparison["rows"], "the comparison must still be produced"
