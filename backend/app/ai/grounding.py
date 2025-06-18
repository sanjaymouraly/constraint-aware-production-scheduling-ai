"""Evidence grounding and verification for AI explanations.

The language model never produces scheduling decisions.  It receives a
**structured evidence pack** that is derived from the validated schedule and
must answer from that pack only.  Afterwards every number in the answer is
checked against the pack; unsupported numeric claims are reported and the
answer is marked as unverified.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.scheduling.instance import SchedulingInstance
from app.scheduling.metrics import compute_metrics
from app.scheduling.result import Assignment
from app.scheduling.validator import ValidationReport

NUMBER_RE = re.compile(
    r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(%|h|hr|hrs|hour|hours|min|mins|minute|minutes)?"
)

#: Numbers that are always acceptable (structural, not claims about the plan).
_WHITELIST = {
    0,
    1,
    2,
    3,
    4,
    5,
    10,
    100,
    24,
    60,
    7,
    12,
    365,
    24 * 60,
}


@dataclass
class EvidencePack:
    """Everything the model is allowed to reason about."""

    summary: dict[str, Any] = field(default_factory=dict)
    orders: list[dict[str, Any]] = field(default_factory=list)
    machines: list[dict[str, Any]] = field(default_factory=list)
    operators: list[dict[str, Any]] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    comparison: dict[str, Any] | None = None
    disruption: dict[str, Any] | None = None
    question: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "summary": self.summary,
            "orders": self.orders,
            "machines": self.machines,
            "operators": self.operators,
            "validation": self.validation,
            "diagnostics": self.diagnostics,
            "question": self.question,
        }
        if self.comparison is not None:
            payload["comparison"] = self.comparison
        if self.disruption is not None:
            payload["disruption"] = self.disruption
        return payload

    def allowed_numbers(self) -> set[float]:
        numbers: set[float] = set()
        for value in _collect_numbers(self.to_dict()):
            numbers.add(value)
            numbers.add(round(value, 1))
            numbers.add(round(value, 2))
            numbers.add(abs(value))
        return numbers


def _collect_numbers(payload: Any) -> list[float]:
    numbers: list[float] = []
    if isinstance(payload, dict):
        for value in payload.values():
            numbers.extend(_collect_numbers(value))
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            numbers.extend(_collect_numbers(value))
    elif isinstance(payload, bool):
        pass
    elif isinstance(payload, (int, float)):
        numbers.append(float(payload))
    elif isinstance(payload, str):
        for match in re.findall(r"-?\d+(?:\.\d+)?", payload.replace(",", ".")):
            try:
                numbers.append(float(match))
            except ValueError:
                continue
    return numbers


def build_evidence(
    instance: SchedulingInstance,
    assignments: list[Assignment],
    validation: ValidationReport,
    metrics: dict[str, Any] | None = None,
    comparison: dict[str, Any] | None = None,
    question: str = "",
    disruption: dict[str, Any] | None = None,
) -> EvidencePack:
    """Assemble the evidence pack for one explanation request."""
    metrics = metrics or compute_metrics(instance, assignments)
    summary = {
        "status": "validated" if validation.valid else "violations-detected",
        "horizon_start": instance.horizon_start.isoformat(),
        "horizon_end": instance.horizon_end.isoformat(),
        "operations_scheduled": metrics.get("operations_scheduled"),
        "operations_total": metrics.get("operations_total"),
        "orders_scheduled": metrics.get("orders_scheduled"),
        "on_time_delivery_pct": metrics.get("on_time_delivery_pct"),
        "weighted_tardiness": metrics.get("weighted_tardiness"),
        "tardiness_minutes": metrics.get("tardiness_minutes"),
        "makespan_minutes": metrics.get("makespan_minutes"),
        "makespan_hours": metrics.get("makespan_hours"),
        "setup_minutes": metrics.get("setup_minutes"),
        "machine_utilisation_pct": metrics.get("machine_utilisation_pct"),
        "overtime_minutes": metrics.get("overtime_minutes"),
        "operating_cost": metrics.get("operating_cost"),
        "penalty_cost": metrics.get("penalty_cost"),
        "schedule_disruption_pct": metrics.get("schedule_disruption_pct"),
    }
    orders = [
        {
            "work_order_code": row["work_order_code"],
            "work_order_id": row["work_order_id"],
            "priority": row["priority"],
            "due_at": row["due_at"],
            "completion": row["completion"],
            "tardiness_minutes": row["tardiness_minutes"],
            "is_late": row["is_late"],
            "hard_deadline": row["hard_deadline"],
        }
        for row in metrics.get("orders", [])
    ]
    machines = metrics.get("machines", [])
    operators = metrics.get("operators", [])
    bottleneck = metrics.get("bottleneck_machine")
    if bottleneck:
        summary["bottleneck_machine_code"] = bottleneck.get("machine_code")
        summary["bottleneck_utilisation_pct"] = bottleneck.get("utilisation_pct")
    return EvidencePack(
        summary=summary,
        orders=orders,
        machines=machines,
        operators=operators,
        validation=validation.to_dict(),
        diagnostics=[v.to_dict() for v in validation.violations][:25],
        comparison=comparison,
        disruption=disruption,
        question=question,
    )


SYSTEM_PROMPT = (
    "You are the explanation engine of a manufacturing production scheduling "
    "platform. The optimisation engine has already produced the schedule; you "
    "only explain it. You must answer exclusively from the JSON evidence pack "
    "that is provided with the question. Never invent numbers, order codes, "
    "machine ids or time intervals. If the evidence does not contain the "
    "information required to answer, say so explicitly. Respond with a JSON "
    "object using exactly these keys: summary (string), bullets (array of "
    "strings), recommendations (array of strings), evidence_refs (array of "
    "strings naming the evidence keys you used) and unsupported (boolean, "
    "true when the evidence was insufficient)."
)


def build_prompt(evidence: EvidencePack) -> str:
    return (
        "Question: "
        + (evidence.question or "Explain this production plan and its key trade-offs.")
        + "\n\nEvidence pack (JSON):\n"
        + json.dumps(evidence.to_dict(), indent=2, default=str)
    )


@dataclass
class VerificationResult:
    ok: bool
    unsupported_claims: list[str] = field(default_factory=list)
    referenced_ids: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "unsupported_claims": self.unsupported_claims,
            "referenced_ids": self.referenced_ids,
            "notes": self.notes,
        }


def verify_answer(answer: str, evidence: EvidencePack) -> VerificationResult:
    """Check that every numeric claim in *answer* exists in the evidence."""
    allowed = evidence.allowed_numbers()
    unsupported: list[str] = []
    for raw in NUMBER_RE.findall(answer or ""):
        number_text = raw[0] if isinstance(raw, tuple) else raw
        suffix = raw[1] if isinstance(raw, tuple) else ""
        try:
            value = float(str(number_text).replace(",", "."))
        except (TypeError, ValueError):
            continue
        if value in _WHITELIST:
            continue
        if any(abs(value - candidate) < 0.051 for candidate in allowed):
            continue
        # Percentages are frequently rounded to whole numbers.
        if suffix == "%" and any(abs(value - candidate) < 0.51 for candidate in allowed):
            continue
        # Hours vs minutes are sometimes converted by the model.
        if suffix in {"h", "hr", "hrs", "hour", "hours"} and any(
            abs(value * 60 - candidate) < 0.6 for candidate in allowed
        ):
            continue
        if suffix in {"min", "mins", "minute", "minutes"} and any(
            abs(value / 60 - candidate) < 0.011 for candidate in allowed
        ):
            continue
        unsupported.append(f"{number_text}{suffix or ''}")

    referenced = re.findall(r"\b(WO-[A-Za-z0-9\-]+|[A-Z]{2,}-\d{2})\b", answer or "")
    return VerificationResult(
        ok=not unsupported,
        unsupported_claims=sorted(set(unsupported)),
        referenced_ids=sorted(set(referenced)),
        notes=(
            "All numeric claims in the answer are present in the evidence pack."
            if not unsupported
            else "Some numbers in the answer could not be found in the evidence pack."
        ),
    )


def _as_text_list(value: Any) -> list[str]:
    """Coerce a model supplied value into a list of readable strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, dict):
        return [f"{key}: {json.dumps(item, default=str)}" for key, item in value.items()]
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for entry in value:
            if isinstance(entry, str):
                text = entry.strip()
                if text:
                    items.append(text)
            elif entry is not None:
                items.append(json.dumps(entry, default=str))
        return items
    return [str(value)]


def _summarise_off_schema(payload: dict[str, Any]) -> str:
    """Build a readable summary for an answer that does not follow the schema.

    Small local models frequently answer with a different JSON shape.  The
    platform still has to show *something* useful — and has to be transparent
    that the answer was not produced in the agreed format.
    """
    parts: list[str] = []
    for key, value in payload.items():
        if key in {"unsupported", "summary"}:
            continue
        if isinstance(value, (dict, list)):
            parts.append(f"{key}: {json.dumps(value, default=str)}")
        else:
            parts.append(f"{key}: {value}")
    if not parts:
        return "The provider answered without a recognisable explanation."
    return (
        "The provider answered in a different format than requested. "
        "Raw answer: " + "; ".join(parts)
    )


def parse_answer(text: str) -> dict[str, Any]:
    """Parse the structured answer returned by the model.

    The result is always normalised to the documented schema (``summary``,
    ``bullets``, ``recommendations``, ``evidence_refs``, ``unsupported``) so
    that the user interface can always render the answer.  An answer that does
    not follow the schema is preserved verbatim under ``raw_answer`` and
    flagged as ``unsupported``, because it was not produced in the agreed
    structure and therefore cannot be trusted as much as a conforming one.
    """
    if not text:
        return {
            "summary": "The provider returned an empty answer.",
            "bullets": [],
            "recommendations": [],
            "evidence_refs": [],
            "unsupported": True,
        }
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\n?", "", candidate)
        candidate = re.sub(r"\n?```$", "", candidate)
    payload: dict[str, Any] | None = None
    try:
        decoded = json.loads(candidate)
        if isinstance(decoded, dict):
            payload = decoded
    except (ValueError, TypeError):
        pass
    if payload is None:
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if match:
            try:
                decoded = json.loads(match.group(0))
                if isinstance(decoded, dict):
                    payload = decoded
            except (ValueError, TypeError):
                pass
    if payload is None:
        return {
            "summary": candidate,
            "bullets": [],
            "recommendations": [],
            "evidence_refs": [],
            "unsupported": True,
        }
    return normalize_answer(payload)


def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
    """Unwrap a single wrapper object such as ``{"analysis": {...}}``.

    Models frequently wrap their answer in one extra key.  The inner object
    carries the actual content, so it is inspected instead of the wrapper.
    """
    if len(payload) == 1:
        only = next(iter(payload.values()))
        if isinstance(only, dict):
            return only
    return payload


def normalize_answer(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise a decoded model answer to the documented schema."""
    known = {
        "summary",
        "explanation",
        "reasoning",
        "conclusion",
        "answer",
        "bullets",
        "key_findings",
        "findings",
        "recommendations",
        "actions",
        "evidence_refs",
        "risks",
        "unsupported",
    }
    inner = _unwrap(payload)
    conforms = bool(known & set(inner))
    result: dict[str, Any] = dict(payload)

    summary = (
        inner.get("summary")
        or inner.get("explanation")
        or inner.get("reasoning")
        or inner.get("conclusion")
        or inner.get("answer")
    )
    if not isinstance(summary, str) or not summary.strip():
        summary = _summarise_off_schema(inner) if not conforms else ""
    result["summary"] = summary.strip() if isinstance(summary, str) else str(summary)

    bullets = inner.get("bullets")
    if bullets is None:
        bullets = inner.get("key_findings", inner.get("findings"))
    result["bullets"] = _as_text_list(bullets)

    recommendations = inner.get("recommendations")
    if recommendations is None:
        recommendations = inner.get("actions")
    result["recommendations"] = _as_text_list(recommendations)

    result["evidence_refs"] = _as_text_list(inner.get("evidence_refs"))
    result["unsupported"] = bool(inner.get("unsupported")) or not conforms
    if not conforms:
        result["raw_answer"] = payload
    return result
