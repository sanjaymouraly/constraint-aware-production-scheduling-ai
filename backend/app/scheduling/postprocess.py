"""Post-processing shared by every solver.

The *setup gap* of an operation is the idle time between it and the operation
that precedes it on the same machine.  Both solvers therefore go through the
same post-processing so that the reported setup time is identical and can be
cross-checked by the independent validator.
"""

from __future__ import annotations

from app.domain.timeutils import add_minutes
from app.scheduling.instance import SchedulingInstance
from app.scheduling.result import Assignment


def apply_setup_gaps(
    instance: SchedulingInstance, assignments: list[Assignment]
) -> list[Assignment]:
    """Annotate each assignment with the setup gap in front of it."""
    by_machine: dict[int, list[Assignment]] = {}
    for assignment in assignments:
        by_machine.setdefault(assignment.machine_id, []).append(assignment)

    for machine_id, machine_assignments in by_machine.items():
        machine = instance.machines.get(machine_id)
        if machine is None:
            continue
        ordered = sorted(machine_assignments, key=lambda a: (a.start, a.end))
        previous: Assignment | None = None
        for assignment in ordered:
            setup = 0
            if previous is not None:
                setup = machine.setup_for(previous.setup_family, assignment.setup_family)
            assignment.setup_minutes = setup
            assignment.setup_start = add_minutes(assignment.start, -setup) if setup else None
            previous = assignment
    return assignments
