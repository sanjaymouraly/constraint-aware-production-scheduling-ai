"""Authentication endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models import User
from app.schemas.api import LoginRequest, TokenResponse, UserCreate, UserOut
from app.services import audit
from app.services.auth import (
    create_access_token,
    hash_password,
    require_admin,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DbSession) -> TokenResponse:
    user = db.scalars(select(User).where(User.username == payload.username)).first()
    if user is None or not verify_password(payload.password, user.hashed_password):
        # Same message for an unknown user and a wrong password: no user enumeration.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password."
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password."
        )
    user.last_login_at = datetime.now(UTC)
    audit.record(
        db, actor=user.username, action="auth.login", entity_type="user", entity_id=user.id
    )
    token = create_access_token(user)
    return TokenResponse(
        access_token=token,
        expires_in_minutes=60 * 24,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.get("/users", response_model=list[UserOut])
def list_users(db: DbSession, admin: User = Depends(require_admin)) -> list[UserOut]:
    rows = db.scalars(select(User).order_by(User.id)).all()
    return [UserOut.model_validate(row) for row in rows]


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate, db: DbSession, admin: User = Depends(require_admin)
) -> UserOut:
    existing = db.scalars(
        select(User).where((User.username == payload.username) | (User.email == payload.email))
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with that username or email already exists.",
        )
    user = User(
        username=payload.username,
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    db.add(user)
    db.flush()
    audit.record(
        db, actor=admin.username, action="user.create", entity_type="user", entity_id=user.id
    )
    return UserOut.model_validate(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_user(user_id: int, db: DbSession, admin: User = Depends(require_admin)) -> Response:
    """Delete a user account (admin only).

    The audit trail is *not* removed: audit rows reference the user by name,
    not by a foreign key, so the history stays intact.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot delete your own account.",
        )
    username = user.username
    db.delete(user)
    db.flush()
    audit.record(
        db,
        actor=admin.username,
        action="user.delete",
        entity_type="user",
        details={"username": username},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: dict[str, Any],
    db: DbSession,
    admin: User = Depends(require_admin),
) -> UserOut:
    """Update the role or the active flag of a user (admin only)."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    role = payload.get("role")
    if role is not None:
        if role not in {"admin", "planner", "viewer"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="role must be one of admin, planner, viewer.",
            )
        user.role = role
    is_active = payload.get("is_active")
    if is_active is not None:
        user.is_active = bool(is_active)
    db.flush()
    audit.record(
        db,
        actor=admin.username,
        action="user.update",
        entity_type="user",
        entity_id=user.id,
        details={"role": user.role, "is_active": user.is_active},
    )
    return UserOut.model_validate(user)
