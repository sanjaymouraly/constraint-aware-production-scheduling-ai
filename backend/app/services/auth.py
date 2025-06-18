"""Authentication, password hashing and role based access control."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

_hasher = PasswordHasher()
bearer_scheme = HTTPBearer(auto_error=False)


class Role(str, Enum):
    admin = "admin"
    planner = "planner"
    viewer = "viewer"

    @property
    def rank(self) -> int:
        return {"viewer": 0, "planner": 1, "admin": 2}[self.value]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_access_token(user: User, expires_minutes: int | None = None) -> str:
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=expires_minutes or settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired, please sign in again.",
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token."
        ) from exc


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db, scope="function"),
) -> User:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    user = db.get(User, int(payload.get("sub", 0)))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is not active.")
    return user


def require_roles(*roles: Role):
    """Dependency factory enforcing a minimum role."""

    def _dependency(user: User = Depends(get_current_user)) -> User:
        required = min(role.rank for role in roles) if roles else 0
        if Role(user.role).rank < required:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This action requires one of the roles: "
                    f"{', '.join(sorted(r.value for r in roles))}."
                ),
            )
        return user

    return _dependency


require_admin = require_roles(Role.admin)
require_planner = require_roles(Role.planner, Role.admin)
require_viewer = require_roles(Role.viewer, Role.planner, Role.admin)


def ensure_bootstrap_admin(db: Session) -> User:
    """Create the bootstrap administrator when the user table is empty."""
    existing = db.scalars(select(User).where(User.username == settings.bootstrap_admin_username))
    user = existing.first()
    if user is not None:
        return user
    user = User(
        username=settings.bootstrap_admin_username,
        email=settings.bootstrap_admin_email,
        full_name="Bootstrap Administrator",
        hashed_password=hash_password(settings.bootstrap_admin_password),
        role=Role.admin.value,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def constant_time_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def random_bytes(count: int = 16) -> bytes:
    return os.urandom(count)
