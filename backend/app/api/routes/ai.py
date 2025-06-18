"""AI provider configuration, connection testing and grounded explanations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from app.ai.client import (
    AIClient,
    AIError,
    decrypt_secret,
    encrypt_secret,
    get_ai_settings,
    select_provider,
)
from app.ai.grounding import build_evidence
from app.api.deps import CurrentUser, DbSession
from app.models import AIProvider, Schedule, User
from app.schemas.api import (
    AIExplainRequest,
    AIProviderIn,
    AIProviderOut,
    AISettingsIn,
    AISettingsOut,
)
from app.services import audit
from app.services.auth import require_admin
from app.services.scheduling_service import load_assignments

router = APIRouter(prefix="/ai", tags=["ai"])


def _provider_out(provider: AIProvider) -> AIProviderOut:
    item = AIProviderOut.model_validate(provider)
    item.has_api_key = bool(provider.api_key_encrypted)
    item.masked_api_key = _mask(decrypt_secret(provider.api_key_encrypted))
    return item


def _mask(key: str | None) -> str | None:
    if not key:
        return None
    if len(key) <= 10:
        return "***"
    return f"{key[:4]}…{key[-3:]}"


@router.get("/catalog")
def catalog(_: CurrentUser) -> list[dict[str, Any]]:
    """Static catalogue of the supported provider kinds."""
    from app.ai.providers import provider_catalog

    return provider_catalog()


@router.get("/providers", response_model=list[AIProviderOut])
def list_providers(db: DbSession, _: User = Depends(require_admin)) -> list[AIProviderOut]:
    rows = db.scalars(select(AIProvider).order_by(AIProvider.name)).all()
    return [_provider_out(row) for row in rows]


@router.post("/providers", response_model=AIProviderOut, status_code=status.HTTP_201_CREATED)
def create_provider(
    payload: AIProviderIn, db: DbSession, user: User = Depends(require_admin)
) -> AIProviderOut:
    if db.scalars(select(AIProvider).where(AIProvider.name == payload.name)).first():
        raise HTTPException(status_code=409, detail="A provider with that name already exists.")
    data = payload.model_dump()
    api_key = data.pop("api_key", None)
    provider = AIProvider(**data, api_key_encrypted=encrypt_secret(api_key) if api_key else None)
    db.add(provider)
    db.flush()
    if provider.is_default:
        _clear_other_defaults(db, provider.id)
    audit.record(
        db,
        actor=user.username,
        action="ai.provider.create",
        entity_type="ai_provider",
        entity_id=provider.id,
        details={"kind": provider.kind, "model": provider.model},
    )
    return _provider_out(provider)


@router.patch("/providers/{provider_id}", response_model=AIProviderOut)
def update_provider(
    provider_id: int,
    payload: AIProviderIn,
    db: DbSession,
    user: User = Depends(require_admin),
) -> AIProviderOut:
    provider = db.get(AIProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="AI provider not found.")
    data = payload.model_dump()
    api_key = data.pop("api_key", None)
    for key, value in data.items():
        setattr(provider, key, value)
    if api_key:
        provider.api_key_encrypted = encrypt_secret(api_key)
    db.flush()
    if provider.is_default:
        _clear_other_defaults(db, provider.id)
    return _provider_out(provider)


@router.delete(
    "/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
def delete_provider(provider_id: int, db: DbSession, user: User = Depends(require_admin)) -> None:
    provider = db.get(AIProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="AI provider not found.")
    db.delete(provider)


@router.post("/providers/{provider_id}/test")
def test_provider(
    provider_id: int, db: DbSession, user: User = Depends(require_admin)
) -> dict[str, Any]:
    provider = db.get(AIProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="AI provider not found.")
    client = AIClient(db)
    result = client.test_connection(provider)
    return {
        "ok": result.ok,
        "message": result.message,
        "latency_ms": round(result.latency_ms, 1),
        "models": result.models,
    }


@router.get("/providers/{provider_id}/models")
def provider_models(
    provider_id: int, db: DbSession, user: User = Depends(require_admin)
) -> dict[str, Any]:
    provider = db.get(AIProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="AI provider not found.")
    try:
        models = AIClient(db).list_models(provider)
    except Exception as exc:  # provider errors are user facing
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"models": models}


@router.get("/settings", response_model=AISettingsOut)
def get_settings(db: DbSession, _: User = Depends(require_admin)) -> AISettingsOut:
    return AISettingsOut.model_validate(get_ai_settings(db))


@router.put("/settings", response_model=AISettingsOut)
def update_settings(
    payload: AISettingsIn, db: DbSession, user: User = Depends(require_admin)
) -> AISettingsOut:
    row = get_ai_settings(db)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    db.flush()
    audit.record(db, actor=user.username, action="ai.settings.update", entity_type="ai_settings")
    return AISettingsOut.model_validate(row)


@router.post("/explain")
def explain(
    payload: AIExplainRequest, db: DbSession, user: User = Depends(require_admin)
) -> dict[str, Any]:
    """Explain a persisted schedule using grounded, verified evidence."""
    from app.scheduling.builder import BuildOptions, build_instance
    from app.scheduling.validator import validate_schedule

    client = AIClient(db)
    try:
        provider = select_provider(db, payload.provider_id)
    except AIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    allowed, reason = client.check_budget(provider)
    if not allowed:
        raise HTTPException(status_code=402, detail=reason)

    schedule = db.get(Schedule, payload.schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")

    assignments = load_assignments(db, payload.schedule_id)
    if not assignments:
        raise HTTPException(
            status_code=409, detail="The selected schedule has no operations to explain."
        )

    try:
        instance = build_instance(
            db,
            BuildOptions(
                horizon_start=schedule.horizon_start,
                horizon_end=schedule.horizon_end,
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    validation = validate_schedule(instance, assignments)
    metrics = schedule.metrics or {}
    comparison = None
    if payload.baseline_schedule_id:
        baseline = db.get(Schedule, payload.baseline_schedule_id)
        if baseline is not None:
            comparison = {
                "baseline_schedule_id": baseline.id,
                "baseline_name": baseline.name,
                "baseline_metrics": baseline.metrics,
                "optimised_metrics": metrics,
            }

    evidence = build_evidence(
        instance,
        assignments,
        validation,
        metrics=metrics,
        comparison=comparison,
        question=payload.question,
    )
    try:
        result = client.explain(provider, evidence)
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return result.to_dict()


def _clear_other_defaults(db, provider_id: int) -> None:
    for other in db.scalars(select(AIProvider).where(AIProvider.id != provider_id)).all():
        if other.is_default:
            other.is_default = False
    db.flush()
