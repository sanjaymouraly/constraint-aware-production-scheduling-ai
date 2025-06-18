"""CP-SAT optimised scheduler (Google OR-Tools).

The model is built directly from an immutable
:class:`~app.scheduling.instance.SchedulingInstance`, therefore the optimiser
works with **exactly** the same data and hard constraints as the FCFS
baseline.

Modelling decisions (documented, testable assumptions)

* **Time** — integer minutes counted from ``instance.horizon_start``.
* **Calendars** — an operation must fit entirely inside one working segment of
  the machine/operator calendar.  The legal start instants of a task are
  therefore a union of intervals, expressed with ``Domain.from_intervals``.
  Maintenance windows, breakdowns and absences are already removed from the
  resource availability, so they are respected automatically.
* **Machine capacity** — ``AddNoOverlap`` over the optional operation
  intervals of every machine.
* **Labour** — every operation needs a qualified, eligible operator; operators
  get their own ``AddNoOverlap``.  Crew sizes above one require that many
  *distinct* operators.
* **Material availability** — a time-phased cumulative constraint: at every
  material receipt boundary the total quantity consumed by operations that have
  already started must not exceed the stock available at that moment.
* **Precedence** — routing precedence with transfer times.
* **Deadlines** — *hard* deadlines are hard constraints; ordinary due dates
  only feed the objective.
* **Setups** — sequence dependent setup times between product families.  The
  machine sequence is modelled as a Hamiltonian chain over the operations
  assigned to that machine.  The chain links are also temporally ordered
  (``start[b] >= end[a] + setup(a, b)``), therefore the chain links are exactly
  the time-consecutive operations and the enforced gap is the true setup gap.
  Machines with very many candidate operations fall back to a pairwise
  disjunction model, which is equivalent provided the setup matrix satisfies
  the triangle inequality (guaranteed by the seed data and a unit test).
* **Objective** — a weighted sum of normalised terms by default; a
  ``lexicographic`` mode optimises the terms one after another while fixing
  the previously achieved optimal value.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ortools.sat.python import cp_model

from app.domain.timeutils import add_minutes, ensure_utc
from app.scheduling.instance import MachineResource, SchedulingInstance, Task
from app.scheduling.result import Assignment, Diagnostic, ScheduleResult

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS: dict[str, float] = {
    "tardiness": 1.0,
    "makespan": 0.1,
    "setup": 0.05,
    "overtime": 0.2,
    "stability": 0.3,
    "flow": 0.0,
}

OBJECTIVE_PRESETS: dict[str, dict[str, float]] = {
    "weighted_tardiness": {"tardiness": 1.0, "makespan": 0.1, "setup": 0.05, "overtime": 0.2},
    "minimize_tardiness": {"tardiness": 1.0, "makespan": 0.0, "setup": 0.0, "overtime": 0.0},
    "minimize_makespan": {"tardiness": 0.2, "makespan": 1.0, "setup": 0.1, "overtime": 0.1},
    "minimize_setup": {"tardiness": 0.5, "makespan": 0.2, "setup": 1.0, "overtime": 0.1},
    "minimize_cost": {"tardiness": 0.5, "makespan": 0.1, "setup": 0.1, "overtime": 1.0},
    "balanced": {
        "tardiness": 1.0,
        "makespan": 0.3,
        "setup": 0.2,
        "overtime": 0.3,
        "stability": 0.3,
    },
}

OBJECTIVE_TERMS = ("tardiness", "makespan", "setup", "overtime", "stability", "flow")

#: Material quantities are scaled to integers for the CP-SAT model.
MATERIAL_SCALE = 1000


@dataclass
class CpSatOptions:
    time_limit_seconds: float = 20.0
    workers: int = 2
    random_seed: int = 42
    objective_name: str = "weighted_tardiness"
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    objective_mode: str = "weighted"  # weighted | lexicographic
    log_search: bool = False
    #: Maximum number of candidate operations per machine before the pairwise
    #: fallback is used for that machine.
    chain_limit_per_machine: int = 60
    #: Optional reference plan for the schedule-stability objective.
    stability_reference: dict[str, tuple[datetime, int | None]] | None = None
    #: Optional warm-start schedule (e.g. the FCFS baseline).
    hint_assignments: list[Assignment] | None = None
    #: Optional cooperative cancellation hook.  It is polled by the solver
    #: callback; returning ``True`` stops the search and the partial (but
    #: valid) solution is returned with status ``CANCELLED``.
    cancel_check: Callable[[], bool] | None = None
    #: CP-SAT "probing" presolve level.  The default (2) runs an expensive
    #: probing pass that costs several seconds on large models and delays the
    #: first solution; it is therefore disabled automatically above
    #: :attr:`probe_variable_threshold` variables.
    probing_level: int | None = None
    #: Number of model variables above which probing is switched off.
    probe_variable_threshold: int = 20_000
    #: Model symmetry level.  ``None`` keeps the CP-SAT default (2).
    symmetry_level: int | None = None
    #: Optional *deterministic* search budget.  Unlike a wall-clock limit this
    #: budget counts the solver's internal work, so the run performs the same
    #: search on every machine and the result is reproducible independently of
    #: the host's speed.  It is used **instead of** the wall-clock limit when
    #: it is set.
    deterministic_time_limit: float | None = None

    def resolved_weights(self) -> dict[str, float]:
        if self.objective_name in OBJECTIVE_PRESETS:
            merged = dict(DEFAULT_WEIGHTS)
            merged.update(OBJECTIVE_PRESETS[self.objective_name])
            merged.update(self.weights)
            return merged
        return {**DEFAULT_WEIGHTS, **self.weights}


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------


class CpSatScheduler:
    """Builds and solves the CP-SAT model."""

    engine_name = "cpsat"

    def __init__(self, instance: SchedulingInstance, options: CpSatOptions | None = None) -> None:
        self.instance = instance
        self.options = options or CpSatOptions()
        # OR-Tools keeps the established CamelCase API as dynamic aliases to
        # its typed snake_case implementation.  Mypy cannot see those aliases
        # in OR-Tools 9.15's stubs, so the mutable model handle is Any; all
        # domain data, options and solver results remain explicitly typed.
        self.model: Any = cp_model.CpModel()
        self.diagnostics: list[Diagnostic] = []
        self.horizon_minutes = max(
            1, int((instance.horizon_end - instance.horizon_start).total_seconds() // 60)
        )
        self._t = instance.horizon_start
        self._task_index = {t.key: t for t in instance.tasks}
        self._order_tasks: dict[int, list[Task]] = {}
        for task in instance.tasks:
            self._order_tasks.setdefault(task.work_order_id, []).append(task)
        for tasks in self._order_tasks.values():
            tasks.sort(key=lambda t: t.sequence)

        self.start: dict[str, cp_model.IntVar] = {}
        self.end: dict[str, cp_model.IntVar] = {}
        self.machine_lit: dict[tuple[str, int], cp_model.IntVar] = {}
        self.operator_lit: dict[tuple[str, int], cp_model.IntVar] = {}
        self.operator_start: dict[tuple[str, int], cp_model.IntVar] = {}
        self.moved: dict[str, cp_model.IntVar] = {}
        self.tardiness: dict[int, cp_model.IntVar] = {}
        self.completion: dict[int, cp_model.IntVar] = {}
        self.setup_terms: list[Any] = []
        self.overtime_terms: list[Any] = []
        self._zero = self.model.NewIntVar(0, 0, "zero")
        self._setup_objective_enabled = True
        self._machine_assignments: dict[str, int] = {}
        # Chain / pairwise setup structures, kept so that a warm start can hint
        # them as well (see :meth:`add_hint`).
        self._chain_succ: dict[int, dict[tuple[str, str], cp_model.IntVar]] = {}
        self._chain_first: dict[int, dict[str, cp_model.IntVar]] = {}
        self._chain_last: dict[int, dict[str, cp_model.IntVar]] = {}
        self._chain_used: dict[int, cp_model.IntVar] = {}
        self._pair_arcs: dict[int, dict[tuple[str, str], cp_model.IntVar]] = {}
        #: ``(task key, material id, check point)`` -> "has the operation
        #: already started at that check point" literal.
        self._material_started: dict[tuple[str, int, int], cp_model.IntVar] = {}

    # -- helpers -----------------------------------------------------------
    def _m(self, moment: datetime) -> int:
        delta = ensure_utc(moment) - self._t
        return int(round(delta.total_seconds() / 60))

    def _dt(self, minutes: int) -> datetime:
        return add_minutes(self._t, minutes)

    @staticmethod
    def _domain(intervals: list[tuple[int, int]]) -> cp_model.Domain:
        if not intervals:
            return cp_model.Domain(0, 0)
        try:
            return cp_model.Domain.from_intervals(intervals)
        except AttributeError:  # pragma: no cover - older OR-Tools
            return cp_model.Domain.FromIntervals(intervals)

    def _machine_ranges(self, task: Task) -> list[tuple[int, int]]:
        """Legal start windows of every machine that can run ``task``."""
        ranges: list[tuple[int, int]] = []
        for machine in self.instance.eligible_machines(task):
            if (task.key, machine.id) not in self.machine_lit:
                continue
            ranges.extend(self._start_ranges(machine.availability, task.duration_minutes))
        return ranges

    @staticmethod
    def _ranges_contained(inner: list[tuple[int, int]], outer: list[tuple[int, int]]) -> bool:
        """True when every window of ``inner`` is covered by ``outer``."""
        return all(any(olo <= ilo and ihi <= ohi for olo, ohi in outer) for ilo, ihi in inner)

    def _start_ranges(self, availability, duration: int) -> list[tuple[int, int]]:
        """Legal start instants (minutes from horizon start) for a block."""
        ranges: list[tuple[int, int]] = []
        for segment in availability.working:
            lo = max(0, self._m(segment.start))
            hi = self._m(segment.end) - duration
            if hi >= lo:
                ranges.append((lo, hi))
        ranges.sort()
        merged: list[tuple[int, int]] = []
        for lo, hi in ranges:
            if merged and lo <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        return merged

    # -- model building ----------------------------------------------------
    def build(self) -> None:
        self._build_task_variables()
        self._build_machine_constraints()
        self._build_operator_constraints()
        self._build_precedence()
        self._build_release_and_deadlines()
        self._build_material_constraints()
        self._build_setup_constraints()
        if self.options.stability_reference:
            self._build_stability(self.options.stability_reference)
        self._build_objective()
        if self.options.hint_assignments:
            self.add_hint(self.options.hint_assignments)

    def _build_task_variables(self) -> None:
        for task in self.instance.tasks:
            start = self.model.NewIntVar(0, self.horizon_minutes, f"start[{task.key}]")
            end = self.model.NewIntVar(0, self.horizon_minutes, f"end[{task.key}]")
            self.start[task.key] = start
            self.end[task.key] = end
            self.model.Add(end == start + task.duration_minutes)

    def _build_machine_constraints(self) -> None:
        for machine in self.instance.machines.values():
            intervals: list[cp_model.IntervalVar] = []
            for task in self.instance.tasks:
                if machine.id not in task.eligible_machine_ids:
                    continue
                if machine.id in self.instance.machine_exclusions:
                    continue
                if (
                    machine.supported_families
                    and task.setup_family not in machine.supported_families
                ):
                    continue
                ranges = self._start_ranges(machine.availability, task.duration_minutes)
                if not ranges:
                    self.diagnostics.append(
                        Diagnostic(
                            code="machine_calendar_too_short",
                            severity="warning",
                            message=(
                                f"Machine {machine.code} has no working window long enough for "
                                f"operation '{task.name}' ({task.duration_minutes} min)."
                            ),
                            task_key=task.key,
                            machine_id=machine.id,
                        )
                    )
                    continue
                lit = self.model.NewBoolVar(f"x[{task.key},{machine.id}]")
                sm = self.model.NewIntVarFromDomain(
                    self._domain(ranges), f"sm[{task.key},{machine.id}]"
                )
                self.machine_lit[(task.key, machine.id)] = lit
                self.model.Add(sm == self.start[task.key]).OnlyEnforceIf(lit)
                intervals.append(
                    self.model.NewOptionalIntervalVar(
                        sm,
                        task.duration_minutes,
                        sm + task.duration_minutes,
                        lit,
                        f"iv[{task.key},{machine.id}]",
                    )
                )
            if intervals:
                self.model.AddNoOverlap(intervals)

        # Every operation is assigned to exactly one machine.
        for task in self.instance.tasks:
            literals = [
                lit for (task_key, _), lit in self.machine_lit.items() if task_key == task.key
            ]
            if not literals:
                continue
            self.model.Add(sum(literals) == 1)

        # Locked operations keep their committed slot.
        for task in self.instance.tasks:
            if not task.locked or task.locked_start is None or task.locked_end is None:
                continue
            for (task_key, machine_id), lit in self.machine_lit.items():
                if task_key != task.key:
                    continue
                if task.locked_machine_id is None or machine_id == task.locked_machine_id:
                    self.model.Add(lit == 1)
                else:
                    self.model.Add(lit == 0)
            self.model.Add(self.start[task.key] == self._m(task.locked_start))
            self.model.Add(self.end[task.key] == self._m(task.locked_end))

    def _build_operator_constraints(self) -> None:
        instance = self.instance
        for task in instance.tasks:
            if not any(
                (task.key, m.id) in self.machine_lit for m in instance.eligible_machines(task)
            ):
                self.diagnostics.append(
                    Diagnostic(
                        code="no_eligible_machine",
                        severity="error",
                        message=(
                            f"Operation '{task.name}' (order {task.work_order_code}) has no machine "
                            f"of type '{task.machine_type}' that can run it inside the horizon."
                        ),
                        task_key=task.key,
                    )
                )
                self.model.Add(self.start[task.key] > self.horizon_minutes)
                continue

            candidates: list[int] = []
            for machine in instance.eligible_machines(task):
                for operator in instance.eligible_operators(task, machine):
                    if operator.id not in candidates:
                        candidates.append(operator.id)

            machine_ranges = self._machine_ranges(task)
            usable: list[int] = []
            for operator_id in candidates:
                operator = instance.operators[operator_id]
                ranges = self._start_ranges(operator.availability, task.duration_minutes)
                if not ranges:
                    continue
                lit = self.model.NewBoolVar(f"u[{task.key},{operator_id}]")
                if self._ranges_contained(machine_ranges, ranges):
                    # The operator is available wherever any eligible machine
                    # is, so the machine calendar already implies the operator
                    # calendar.  The shared task start can be reused, which
                    # keeps the model small (one variable instead of one
                    # union-domain variable per task/operator pair).
                    so = self.start[task.key]
                else:
                    so = self.model.NewIntVarFromDomain(
                        self._domain(ranges), f"so[{task.key},{operator_id}]"
                    )
                    self.model.Add(so == self.start[task.key]).OnlyEnforceIf(lit)
                self.operator_lit[(task.key, operator_id)] = lit
                self.operator_start[(task.key, operator_id)] = so
                usable.append(operator_id)

            if not usable:
                self.diagnostics.append(
                    Diagnostic(
                        code="no_qualified_operator",
                        severity="error",
                        message=(
                            f"No available operator is qualified for operation '{task.name}' "
                            f"(order {task.work_order_code}, required skills: "
                            f"{', '.join(task.required_skills) or 'none'})."
                        ),
                        task_key=task.key,
                    )
                )
                self.model.Add(self.start[task.key] > self.horizon_minutes)
                continue

            self.model.Add(
                sum(self.operator_lit[(task.key, oid)] for oid in usable) == task.crew_size
            )
            if task.crew_size > 1:
                for index, oid_a in enumerate(usable):
                    for oid_b in usable[index + 1 :]:
                        self.model.AddBoolOr(
                            [
                                self.operator_lit[(task.key, oid_a)].Not(),
                                self.operator_lit[(task.key, oid_b)].Not(),
                            ]
                        )

        for operator in instance.operators.values():
            intervals: list[cp_model.IntervalVar] = []
            for task in instance.tasks:
                key = (task.key, operator.id)
                op_lit = self.operator_lit.get(key)
                if op_lit is None:
                    continue
                so = self.operator_start[key]
                intervals.append(
                    self.model.NewOptionalIntervalVar(
                        so,
                        task.duration_minutes,
                        so + task.duration_minutes,
                        op_lit,
                        f"op_iv[{task.key},{operator.id}]",
                    )
                )
            if intervals:
                self.model.AddNoOverlap(intervals)

        for task in instance.tasks:
            for machine in instance.eligible_machines(task):
                machine_lit = self.machine_lit.get((task.key, machine.id))
                if machine_lit is None:
                    continue
                for (task_key, operator_id), lit in self.operator_lit.items():
                    if task_key != task.key:
                        continue
                    operator = instance.operators[operator_id]
                    if not operator.eligible_for_machine(machine):
                        self.model.AddBoolOr([machine_lit.Not(), lit.Not()])

    def _build_precedence(self) -> None:
        for task in self.instance.tasks:
            for pred_key, transfer in task.transfer_after_predecessor:
                self.model.Add(self.end[pred_key] + transfer <= self.start[task.key])

    def _build_release_and_deadlines(self) -> None:
        for task in self.instance.tasks:
            self.model.Add(self.start[task.key] >= max(0, self._m(task.release_at)))
            if task.hard_deadline is not None:
                self.model.Add(self.end[task.key] <= self._m(task.hard_deadline))

    def _build_material_constraints(self) -> None:
        instance = self.instance
        usage: dict[int, list[tuple[str, float]]] = {}
        for task in instance.tasks:
            for material_id, quantity in task.material_requirements:
                usage.setdefault(material_id, []).append((task.key, quantity))

        for material_id, entries in usage.items():
            stock = instance.materials.get(material_id)
            if stock is None:
                continue
            check_points: dict[int, float] = {}
            for arrival, available_before in stock.receipt_boundaries():
                moment = max(0, self._m(arrival) - 1)
                check_points[moment] = min(
                    check_points.get(moment, available_before), available_before
                )
            check_points[self.horizon_minutes] = min(
                check_points.get(self.horizon_minutes, stock.total_available()),
                stock.total_available(),
            )
            for moment, available in sorted(check_points.items()):
                consumed = []
                for task_key, quantity in entries:
                    if quantity <= 0:
                        continue
                    started = self.model.NewBoolVar(f"used[{task_key},{material_id},{moment}]")
                    self.model.Add(self.start[task_key] <= moment).OnlyEnforceIf(started)
                    self.model.Add(self.start[task_key] >= moment + 1).OnlyEnforceIf(started.Not())
                    # Remember the variable so that a warm start can hint it.
                    self._material_started[(task_key, material_id, moment)] = started
                    # CP-SAT needs integer coefficients.  Requirements are
                    # rounded *down* and availability *up* so the constraint
                    # never rejects a schedule that the real inventory allows.
                    consumed.append(int(math.floor(quantity * MATERIAL_SCALE)) * started)
                if not consumed:
                    continue
                limit = int(math.ceil(available * MATERIAL_SCALE))
                self.model.Add(sum(consumed) <= limit)

    def _build_setup_constraints(self) -> None:
        for machine in self.instance.machines.values():
            eligible = [
                task for task in self.instance.tasks if (task.key, machine.id) in self.machine_lit
            ]
            if len(eligible) < 2:
                continue
            has_setup = any(
                machine.setup_for(a.setup_family, b.setup_family) > 0
                for a in eligible
                for b in eligible
                if a.key != b.key
            )
            if not has_setup:
                continue
            if len(eligible) <= self.options.chain_limit_per_machine:
                self._build_machine_chain(machine, eligible)
            else:
                self._build_machine_pairwise(machine, eligible)
                self._setup_objective_enabled = False
                self.diagnostics.append(
                    Diagnostic(
                        code="setup_pairwise_fallback",
                        severity="info",
                        message=(
                            f"Machine {machine.code} has {len(eligible)} candidate operations; "
                            f"the pairwise setup model was used instead of the exact chain model."
                        ),
                        machine_id=machine.id,
                    )
                )

    def _build_machine_chain(self, machine: MachineResource, eligible: list[Task]) -> None:
        succ: dict[tuple[str, str], cp_model.IntVar] = {}
        first: dict[str, cp_model.IntVar] = {
            task.key: self.model.NewBoolVar(f"first[{task.key},{machine.id}]") for task in eligible
        }
        last: dict[str, cp_model.IntVar] = {
            task.key: self.model.NewBoolVar(f"last[{task.key},{machine.id}]") for task in eligible
        }
        for a in eligible:
            for b in eligible:
                if a.key == b.key:
                    continue
                setup = machine.setup_for(a.setup_family, b.setup_family)
                lit = self.model.NewBoolVar(f"succ[{a.key},{b.key},{machine.id}]")
                succ[(a.key, b.key)] = lit
                self.model.AddImplication(lit, self.machine_lit[(a.key, machine.id)])
                self.model.AddImplication(lit, self.machine_lit[(b.key, machine.id)])
                # The chain link is also the temporal order of the two
                # operations, hence it is exactly the setup gap.
                self.model.Add(self.start[b.key] >= self.end[a.key] + setup).OnlyEnforceIf(lit)
                if setup > 0:
                    self.setup_terms.append(setup * lit)
        for task in eligible:
            assigned = self.machine_lit[(task.key, machine.id)]
            successors = [lit for (a, _), lit in succ.items() if a == task.key]
            predecessors = [lit for (_, b), lit in succ.items() if b == task.key]
            if successors:
                self.model.Add(sum(successors) + last[task.key] == assigned)
            else:
                self.model.Add(last[task.key] == assigned)
            if predecessors:
                self.model.Add(sum(predecessors) + first[task.key] == assigned)
            else:
                self.model.Add(first[task.key] == assigned)
        used = self.model.NewBoolVar(f"used[{machine.id}]")
        assigned_total = sum(self.machine_lit[(t.key, machine.id)] for t in eligible)
        self.model.Add(assigned_total >= 1).OnlyEnforceIf(used)
        self.model.Add(assigned_total == 0).OnlyEnforceIf(used.Not())
        self.model.Add(sum(first.values()) == used)
        self.model.Add(sum(last.values()) == used)
        # Keep a reference so that a warm start can hint the whole chain.
        self._chain_succ[machine.id] = succ
        self._chain_first[machine.id] = first
        self._chain_last[machine.id] = last
        self._chain_used[machine.id] = used

    def _build_machine_pairwise(self, machine: MachineResource, eligible: list[Task]) -> None:
        arcs: dict[tuple[str, str], cp_model.IntVar] = {}
        for a in eligible:
            for b in eligible:
                if a.key >= b.key:
                    continue
                setup_ab = machine.setup_for(a.setup_family, b.setup_family)
                setup_ba = machine.setup_for(b.setup_family, a.setup_family)
                if setup_ab <= 0 and setup_ba <= 0:
                    continue
                lit_ab = self.model.NewBoolVar(f"arc[{a.key},{b.key},{machine.id}]")
                lit_ba = self.model.NewBoolVar(f"arc[{b.key},{a.key},{machine.id}]")
                arcs[(a.key, b.key)] = lit_ab
                arcs[(b.key, a.key)] = lit_ba
                for lit in (lit_ab, lit_ba):
                    self.model.AddImplication(lit, self.machine_lit[(a.key, machine.id)])
                    self.model.AddImplication(lit, self.machine_lit[(b.key, machine.id)])
                # The order of the pair only matters when *both* operations
                # actually run on this machine.
                self.model.AddBoolOr(
                    [
                        self.machine_lit[(a.key, machine.id)].Not(),
                        self.machine_lit[(b.key, machine.id)].Not(),
                        lit_ab,
                        lit_ba,
                    ]
                )
                if setup_ab > 0:
                    self.model.Add(self.start[b.key] >= self.end[a.key] + setup_ab).OnlyEnforceIf(
                        lit_ab
                    )
                if setup_ba > 0:
                    self.model.Add(self.start[a.key] >= self.end[b.key] + setup_ba).OnlyEnforceIf(
                        lit_ba
                    )
        self._pair_arcs[machine.id] = arcs

    def add_hint(self, assignments: list[Assignment]) -> int:
        """Warm-start the solver with a known feasible schedule.

        A valid hint lets CP-SAT start from a feasible solution and spend its
        time *improving* it instead of searching for the first one.

        The hint must be **complete**, otherwise CP-SAT has to complete it
        itself, which on large instances costs more time than the search.  It
        therefore covers every decision variable of the model:

        * the machine literals ``x[task, machine]``,
        * the operator literals ``u[task, operator]`` and the operator start
          variables,
        * the start variable of every task,
        * the setup chain of every machine (successor arcs, first/last flags
          and the "machine used" flag) or, in the pairwise fallback, the
          ordering arcs.

        The chain of a machine is the time order of the operations that run on
        it, so the hint is simply "the previous operation followed by the next
        one".
        """
        if not assignments:
            return 0
        chosen_machine = {a.task_key: a.machine_id for a in assignments}
        chosen_operator = {a.task_key: a.operator_id for a in assignments}
        start_minutes = {a.task_key: self._m(a.start) for a in assignments}
        hints = 0

        for assignment in assignments:
            key = assignment.task_key
            minutes = start_minutes[key]
            for (task_key, machine_id), lit in self.machine_lit.items():
                if task_key != key:
                    continue
                value = 1 if machine_id == chosen_machine.get(key) else 0
                self.model.AddHint(lit, value)
                hints += 1
            for (task_key, operator_id), lit in self.operator_lit.items():
                if task_key != key:
                    continue
                value = 1 if operator_id == chosen_operator.get(key) else 0
                self.model.AddHint(lit, value)
                hints += 1
                # The operator start variable only exists when the operator
                # calendar is *not* a superset of the machine calendar.
                operator_start = self.operator_start.get((task_key, operator_id))
                if operator_start is not None and operator_start is not self.start.get(task_key):
                    self.model.AddHint(operator_start, minutes)
                    hints += 1
            if key in self.start:
                self.model.AddHint(self.start[key], minutes)
                hints += 1

        hints += self._hint_machine_chains(chosen_machine, start_minutes)
        hints += self._hint_material_usage(start_minutes)
        return hints

    def _hint_material_usage(self, start_minutes: dict[str, int]) -> int:
        """Hint the "operation already started" literals of the inventory model."""
        hints = 0
        for (task_key, _material_id, moment), lit in self._material_started.items():
            start = start_minutes.get(task_key)
            if start is None:
                continue
            self.model.AddHint(lit, 1 if start <= moment else 0)
            hints += 1
        return hints

    def _hint_machine_chains(
        self,
        chosen_machine: dict[str, int],
        start_minutes: dict[str, int],
    ) -> int:
        """Hint the setup structure (chain or pairwise arcs) of every machine."""
        hints = 0
        by_machine: dict[int, list[str]] = {}
        for task_key, machine_id in chosen_machine.items():
            by_machine.setdefault(machine_id, []).append(task_key)

        for machine_id in set(by_machine) | set(self._chain_succ) | set(self._pair_arcs):
            ordered = sorted(
                by_machine.get(machine_id, []), key=lambda k: (start_minutes.get(k, 0), k)
            )
            chain = self._chain_succ.get(machine_id)
            if chain is not None:
                first = self._chain_first.get(machine_id, {})
                last = self._chain_last.get(machine_id, {})
                used = self._chain_used.get(machine_id)
                position = {key: index for index, key in enumerate(ordered)}
                # Only the time-consecutive operations are linked.
                linked_pairs = {
                    (ordered[index], ordered[index + 1]) for index in range(len(ordered) - 1)
                }
                for (a_key, b_key), lit in chain.items():
                    self.model.AddHint(lit, 1 if (a_key, b_key) in linked_pairs else 0)
                    hints += 1
                for task_key, lit in first.items():
                    self.model.AddHint(lit, 1 if position.get(task_key) == 0 else 0)
                    hints += 1
                for task_key, lit in last.items():
                    self.model.AddHint(lit, 1 if position.get(task_key) == len(ordered) - 1 else 0)
                    hints += 1
                if used is not None:
                    self.model.AddHint(used, 1 if ordered else 0)
                    hints += 1
                continue

            arcs = self._pair_arcs.get(machine_id)
            if arcs is None:
                continue
            position = {key: index for index, key in enumerate(ordered)}
            for (a_key, b_key), lit in arcs.items():
                if a_key not in position or b_key not in position:
                    self.model.AddHint(lit, 0)
                else:
                    self.model.AddHint(lit, 1 if position[a_key] < position[b_key] else 0)
                hints += 1
        return hints

    def _build_stability(self, reference: dict[str, tuple[datetime, int | None]]) -> None:
        for task in self.instance.tasks:
            ref = reference.get(task.key)
            if ref is None:
                continue
            base_start, base_machine = ref
            moved = self.model.NewBoolVar(f"moved[{task.key}]")
            self.moved[task.key] = moved
            base = self._m(base_start)
            after = self.model.NewBoolVar(f"after[{task.key}]")
            before = self.model.NewBoolVar(f"before[{task.key}]")
            self.model.Add(self.start[task.key] >= base + 1).OnlyEnforceIf(after)
            self.model.Add(self.start[task.key] <= base - 1).OnlyEnforceIf(before)
            self.model.AddBoolOr([after, before]).OnlyEnforceIf(moved)
            self.model.AddBoolOr([after, before, moved.Not()])
            if base_machine is not None:
                for (task_key, machine_id), lit in self.machine_lit.items():
                    if task_key != task.key:
                        continue
                    if machine_id == base_machine:
                        self.model.AddBoolOr([moved.Not(), lit])
                    else:
                        self.model.AddBoolOr([moved.Not(), lit.Not()])

    # -- objective ---------------------------------------------------------
    def _build_objective(self) -> None:
        instance = self.instance
        weights = self.options.resolved_weights()

        for order_id, tasks in self._order_tasks.items():
            completion = self.model.NewIntVar(0, self.horizon_minutes, f"completion[{order_id}]")
            self.model.AddMaxEquality(completion, [self.end[t.key] for t in tasks])
            self.completion[order_id] = completion
            due = max(self._m(t.due_at) for t in tasks)
            tard = self.model.NewIntVar(0, self.horizon_minutes, f"tardiness[{order_id}]")
            self.model.AddMaxEquality(tard, [self._zero, completion - due])
            self.tardiness[order_id] = tard

        weighted_tardiness = []
        for order_id, tard in self.tardiness.items():
            tasks = self._order_tasks[order_id]
            priority_weight = max(t.priority for t in tasks) * 10
            weighted_tardiness.append(priority_weight * tard)

        makespan = self.model.NewIntVar(0, self.horizon_minutes, "makespan")
        self.model.AddMaxEquality(makespan, [self.end[t.key] for t in instance.tasks])

        for operator in instance.operators.values():
            work = []
            for task in instance.tasks:
                lit = self.operator_lit.get((task.key, operator.id))
                if lit is not None:
                    work.append(task.duration_minutes * lit)
            if not work:
                continue
            capacity = operator.availability.total_working_minutes
            ot = self.model.NewIntVar(0, max(capacity, 0), f"overtime[{operator.id}]")
            self.model.AddMaxEquality(ot, [self._zero, sum(work) - capacity])
            self.overtime_terms.append(ot)

        flow = []
        for task in instance.tasks:
            flow.append(self.end[task.key] - max(0, self._m(task.release_at)))

        terms: list[Any] = []
        if weights.get("tardiness"):
            terms.append(int(round(weights["tardiness"] * 100)) * sum(weighted_tardiness))
        if weights.get("makespan"):
            terms.append(int(round(weights["makespan"] * 100)) * makespan)
        if weights.get("setup") and self.setup_terms and self._setup_objective_enabled:
            terms.append(int(round(weights["setup"] * 100)) * sum(self.setup_terms))
        if weights.get("overtime") and self.overtime_terms:
            terms.append(int(round(weights["overtime"] * 100)) * sum(self.overtime_terms))
        if weights.get("flow"):
            terms.append(int(round(weights["flow"] * 100)) * sum(flow))
        if weights.get("stability") and self.moved:
            terms.append(int(round(weights["stability"] * 100)) * sum(self.moved.values()))

        if not terms:
            terms.append(makespan)
        self.model.Minimize(sum(terms))
        self._objective_terms = terms

    # -- solving -----------------------------------------------------------
    def _apply_presolve_parameters(self, solver: cp_model.CpSolver) -> None:
        """Tune the presolve for the size of the model.

        CP-SAT's *probing* presolve level (2 by default) tries to detect
        dominated and fixed variables.  On the sequencing models of this
        platform it costs many seconds and delays the first solution by so
        much that a time limited run can end without any incumbent at all.
        It is therefore switched off automatically for large models, while
        small models keep the stronger (and cheap) presolve.
        """
        probing = self.options.probing_level
        if probing is None:
            variable_count = len(self.model.Proto().variables)
            probing = 0 if variable_count > self.options.probe_variable_threshold else 2
        if probing != solver.parameters.cp_model_probing_level:
            solver.parameters.cp_model_probing_level = probing
        if self.options.symmetry_level is not None:
            solver.parameters.symmetry_level = self.options.symmetry_level

    def solve(self) -> ScheduleResult:
        started = time.perf_counter()
        solver = cp_model.CpSolver()
        if self.options.deterministic_time_limit is not None:
            # A deterministic budget makes the run reproducible on every host:
            # the solver stops after the same amount of internal work, not
            # after the same amount of wall-clock time.
            solver.parameters.max_deterministic_time = float(self.options.deterministic_time_limit)
            solver.parameters.max_time_in_seconds = float(self.options.time_limit_seconds)
        else:
            solver.parameters.max_time_in_seconds = float(self.options.time_limit_seconds)
        solver.parameters.num_search_workers = max(1, int(self.options.workers))
        solver.parameters.random_seed = int(self.options.random_seed)
        solver.parameters.log_search_progress = bool(self.options.log_search)
        self._apply_presolve_parameters(solver)

        cancel_check = self.options.cancel_check
        cancelled = False
        if cancel_check is not None:
            # The callback is a closure, so the narrowed callable is copied to
            # a local that keeps its non-optional type for the type checker.
            checker = cancel_check

            class _CancelCallback(cp_model.CpSolverSolutionCallback):
                def on_solution_callback(self) -> None:  # pragma: no cover - solver callback
                    nonlocal cancelled
                    if checker():
                        cancelled = True
                        self.stop_search()

            callback = _CancelCallback()
            status = solver.Solve(self.model, callback)
        else:
            status = solver.Solve(self.model)
        runtime_ms = (time.perf_counter() - started) * 1000.0

        if status == cp_model.OPTIMAL:
            solver_status = "OPTIMAL"
        elif status == cp_model.FEASIBLE:
            solver_status = "CANCELLED" if cancelled else "FEASIBLE"
        elif status == cp_model.INFEASIBLE:
            solver_status = "INFEASIBLE"
        elif status == cp_model.MODEL_INVALID:
            solver_status = "ERROR"
        else:
            solver_status = "UNKNOWN"

        assignments: list[Assignment] = []
        if solver_status in {"OPTIMAL", "FEASIBLE", "CANCELLED"}:
            assignments = self._extract(solver)
            missing = [key for key in self.start if key not in {a.task_key for a in assignments}]
            if missing:
                solver_status = "ERROR"
                self.diagnostics.append(
                    Diagnostic(
                        code="extraction_incomplete",
                        severity="error",
                        message=(
                            f"{len(missing)} operations could not be read back from the solver "
                            f"solution; the schedule was discarded."
                        ),
                        details={"missing": missing[:20]},
                    )
                )
        elif solver_status == "INFEASIBLE":
            self.diagnostics.append(
                Diagnostic(
                    code="model_infeasible",
                    severity="error",
                    message=(
                        "No schedule satisfies all hard constraints for this horizon. Resolve a "
                        "material shortage, add capacity, extend the horizon or renegotiate a "
                        "mandatory deadline."
                    ),
                )
            )
        elif solver_status == "CANCELLED":
            self.diagnostics.append(
                Diagnostic(
                    code="solver_cancelled",
                    severity="warning",
                    message=(
                        "The run was cancelled by the operator. The best feasible solution "
                        "found so far is reported."
                    ),
                )
            )
        elif solver_status == "UNKNOWN":
            self.diagnostics.append(
                Diagnostic(
                    code="solver_timeout",
                    severity="warning",
                    message=(
                        "The solver reached its time limit without proving optimality or "
                        "infeasibility."
                    ),
                )
            )

        objective_value = None
        bound = None
        if solver_status in {"OPTIMAL", "FEASIBLE", "CANCELLED"}:
            objective_value = float(solver.ObjectiveValue())
            bound = float(solver.BestObjectiveBound())

        return ScheduleResult(
            engine=self.engine_name,
            status=solver_status,  # type: ignore[arg-type]
            assignments=assignments,
            unscheduled=[],
            objective_value=objective_value,
            best_bound=bound,
            runtime_ms=runtime_ms,
            diagnostics=list(self.diagnostics),
            solver_log=solver.ResponseStats() if self.options.log_search else "",
            horizon_start=self.instance.horizon_start,
            horizon_end=self.instance.horizon_end,
            parameters={
                "time_limit_seconds": self.options.time_limit_seconds,
                "deterministic_time_limit": self.options.deterministic_time_limit,
                "workers": self.options.workers,
                "random_seed": self.options.random_seed,
                "objective_name": self.options.objective_name,
                "weights": self.options.resolved_weights(),
                "objective_mode": self.options.objective_mode,
            },
        )

    def _extract(self, solver: cp_model.CpSolver) -> list[Assignment]:
        assignments: list[Assignment] = []
        for task in self.instance.tasks:
            machine_id = None
            for (key, mid), lit in self.machine_lit.items():
                if key == task.key and solver.Value(lit):
                    machine_id = mid
                    break
            if machine_id is None:
                continue
            operator_id = None
            for (key, oid), lit in self.operator_lit.items():
                if key == task.key and solver.Value(lit):
                    operator_id = oid
                    break
            machine = self.instance.machines[machine_id]
            start = self._dt(solver.Value(self.start[task.key]))
            end = self._dt(solver.Value(self.end[task.key]))
            assignments.append(
                Assignment(
                    task_key=task.key,
                    work_order_id=task.work_order_id,
                    work_order_code=task.work_order_code,
                    operation_id=task.operation_id,
                    sequence=task.sequence,
                    name=task.name,
                    machine_id=machine_id,
                    machine_code=machine.code,
                    operator_id=operator_id,
                    operator_code=(
                        self.instance.operators[operator_id].code if operator_id else None
                    ),
                    start=start,
                    end=end,
                    processing_minutes=task.duration_minutes,
                    is_locked=task.locked,
                    setup_family=task.setup_family,
                )
            )
        assignments.sort(key=lambda a: a.start)
        return assignments


def run_cpsat(instance: SchedulingInstance, options: CpSatOptions | None = None) -> ScheduleResult:
    scheduler = CpSatScheduler(instance, options)
    scheduler.build()
    return scheduler.solve()
