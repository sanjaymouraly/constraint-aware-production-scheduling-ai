"""Audit trail helper."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(
    session: Session,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str | int | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=details or {},
    )
    session.add(entry)
    session.flush()
    return entry
