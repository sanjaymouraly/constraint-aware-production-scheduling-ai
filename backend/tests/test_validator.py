"""Tests for the independent schedule validator.

The validator is the safety net of the whole platform: every schedule that is
produced by the FCFS baseline, by CP-SAT or by a scenario re-plan must pass it
before it can be published.  These tests deliberately *break* a valid plan in
every possible way and assert that the validator notices.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import timedelta

import pytest
from app.scheduling.fcfs import run_fcfs
from app.scheduling.instance import SchedulingInstance
from app.scheduling.postprocess import apply_setup_gaps
from app.scheduling.result import Assignment
from app.scheduling.validator import validate_schedule


@pytest.fixture()
def valid_plan(instance: SchedulingInstance) -> list[Assignment]:
    assignments = run_fcfs(instance).assignments
    apply_setup_gaps(instance, assignments)
    report = validate_schedule(instance, assignments)
    assert report.valid, [v.to_dict() for v in report.violations]
    return assignments


def _codes(report) -> set[str]:
    return {violation.code for violation in report.violations}


class TestValidatorAcceptsGoodPlans:
    def test_valid_plan_has_no_violations(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        report = validate_schedule(instance, valid_plan)
        assert report.valid
        assert report.violation_count == 0
        assert report.checked_operations == len(instance.tasks)

    def test_validator_reports_the_constraints_it_checks(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        report = validate_schedule(instance, valid_plan)
        assert len(report.checked_constraints) == 14
        for index, constraint in enumerate(report.checked_constraints, start=1):
            assert constraint.startswith(f"H{index} ")


class TestValidatorDetectsViolations:
    def test_missing_task(self, instance: SchedulingInstance, valid_plan: list[Assignment]) -> None:
        broken = valid_plan[:-1]
        report = validate_schedule(instance, broken)
        assert not report.valid
        assert "task_not_scheduled" in _codes(report)

    def test_task_scheduled_twice(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        broken = list(valid_plan) + [valid_plan[0]]
        report = validate_schedule(instance, broken)
        assert "task_scheduled_twice" in _codes(report)

    def test_unknown_task(self, instance: SchedulingInstance, valid_plan: list[Assignment]) -> None:
        broken = list(valid_plan)
        broken[0] = replace(broken[0], task_key="does-not-exist")
        report = validate_schedule(instance, broken)
        assert "unknown_task" in _codes(report)

    def test_outside_horizon(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        broken = list(valid_plan)
        broken[0] = replace(
            broken[0],
            start=broken[0].start - timedelta(days=30),
            end=broken[0].end - timedelta(days=30),
        )
        report = validate_schedule(instance, broken)
        assert "outside_horizon" in _codes(report)

    def test_duration_mismatch(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        broken = list(valid_plan)
        broken[0] = replace(broken[0], end=broken[0].end + timedelta(minutes=17))
        report = validate_schedule(instance, broken)
        assert "duration_mismatch" in _codes(report)

    def test_machine_not_eligible(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        """Assign a task to a machine of the wrong type."""
        task = instance.task_by_key(valid_plan[0].task_key)
        wrong = next(
            machine
            for machine in instance.machines.values()
            if machine.machine_type != task.machine_type
        )
        broken = list(valid_plan)
        broken[0] = replace(
            broken[0],
            machine_id=wrong.id,
            machine_code=wrong.code,
        )
        report = validate_schedule(instance, broken)
        assert _codes(report) & {"machine_not_eligible", "machine_type_mismatch"}

    def test_machine_overlap(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        """Move one operation on top of another one of the same machine."""
        by_machine: dict[int, list[Assignment]] = {}
        for assignment in valid_plan:
            by_machine.setdefault(assignment.machine_id, []).append(assignment)
        pair = next(rows for rows in by_machine.values() if len(rows) >= 2)
        first, second = pair[0], pair[1]
        broken = list(valid_plan)
        for index, assignment in enumerate(broken):
            if assignment.task_key == second.task_key:
                broken[index] = replace(
                    assignment,
                    start=first.start,
                    end=first.end,
                )
        report = validate_schedule(instance, broken)
        assert "machine_overlap" in _codes(report)

    def test_released_too_early(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        broken = list(valid_plan)
        broken[0] = replace(
            broken[0],
            start=broken[0].start - timedelta(days=5),
            end=broken[0].end - timedelta(days=5),
        )
        report = validate_schedule(instance, broken)
        assert _codes(report) & {"released_too_early", "outside_horizon"}

    def test_operator_not_qualified(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        """Assign an operator without the required skills."""
        target = next(a for a in valid_plan if a.operator_id is not None)
        task = instance.task_by_key(target.task_key)
        if not task.required_skills:
            pytest.skip("the demo factory must contain a task with required skills")
        unqualified = next(
            operator
            for operator in instance.operators.values()
            if not set(task.required_skills).issubset(operator.skills)
        )
        broken = list(valid_plan)
        for index, assignment in enumerate(broken):
            if assignment.task_key == target.task_key:
                broken[index] = replace(
                    assignment,
                    operator_id=unqualified.id,
                    operator_code=unqualified.code,
                )
        report = validate_schedule(instance, broken)
        assert "operator_not_qualified" in _codes(report)

    def test_no_operator_assigned(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        broken = list(valid_plan)
        broken[0] = replace(broken[0], operator_id=None, operator_code=None)
        report = validate_schedule(instance, broken)
        assert "no_operator_assigned" in _codes(report)

    def test_precedence_violated(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        """Move a successor operation before its predecessor."""
        successor = next(
            task
            for task in instance.tasks
            if task.predecessor_keys and task.key in {a.task_key for a in valid_plan}
        )
        broken = list(valid_plan)
        for index, assignment in enumerate(broken):
            if assignment.task_key == successor.key:
                broken[index] = replace(
                    assignment,
                    start=instance.horizon_start + timedelta(minutes=1),
                    end=instance.horizon_start + timedelta(minutes=2),
                )
        report = validate_schedule(instance, broken)
        assert "precedence_violated" in _codes(report) or "outside_working_calendar" in _codes(
            report
        )

    def test_locked_operation_moved(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        """A locked operation must keep its committed slot."""
        locked = next(
            (
                task
                for task in instance.tasks
                if task.locked and task.locked_start and task.locked_end
            ),
            None,
        )
        if locked is None:
            # Build a locked variant of the instance to exercise the rule.
            tasks = []
            for task in instance.tasks:
                if task is instance.tasks[0]:
                    tasks.append(
                        replace(
                            task,
                            locked=True,
                            locked_start=task.release_at + timedelta(hours=1),
                            locked_end=task.release_at + timedelta(hours=2),
                            locked_machine_id=task.eligible_machine_ids[0],
                        )
                    )
                else:
                    tasks.append(task)
            locked_instance = replace(instance, tasks=tasks)
            assignments = run_fcfs(locked_instance).assignments
            apply_setup_gaps(locked_instance, assignments)
            broken = list(assignments)
            target = broken[0]
            broken[0] = replace(
                target,
                start=target.start + timedelta(minutes=30),
                end=target.end + timedelta(minutes=30),
            )
            report = validate_schedule(locked_instance, broken)
            assert "locked_operation_moved" in _codes(report)
            return
        pytest.skip("unreachable")

    def test_material_shortage(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        """Remove all material stock and the plan must be reported as short."""
        broken_instance = replace(
            instance,
            materials={
                key: replace(stock, on_hand=0.0, receipts=())
                for key, stock in instance.materials.items()
            },
        )
        broken = list(valid_plan)
        report = validate_schedule(broken_instance, broken)
        assert "material_shortage" in _codes(report)

    def test_validator_does_not_share_solver_code(self) -> None:
        """The validator must be independent of both solvers."""
        import inspect

        from app.scheduling import validator

        source = inspect.getsource(validator)
        assert "cpsat" not in source.lower()
        assert "from app.scheduling.fcfs" not in source
        assert "from app.scheduling.cpsat" not in source


class TestValidatorEdgeCases:
    def test_empty_schedule_is_invalid(self, instance: SchedulingInstance) -> None:
        report = validate_schedule(instance, [])
        assert not report.valid
        assert "task_not_scheduled" in _codes(report)

    def test_partial_validation_can_ignore_missing_tasks(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        report = validate_schedule(instance, valid_plan[:-1], require_complete=False)
        assert report.valid

    def test_copy_of_assignments_is_independent(
        self, instance: SchedulingInstance, valid_plan: list[Assignment]
    ) -> None:
        duplicate = copy.deepcopy(valid_plan)
        assert validate_schedule(instance, duplicate).valid
