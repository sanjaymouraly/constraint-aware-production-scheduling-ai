"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.services.auth import get_current_user

# `scope="function"` commits/closes the transaction after the handler returns
# but *before* FastAPI sends the response. This makes a successful POST visible
# to a follow-up GET immediately (especially important for Vite/browser clients).
DbSession = Annotated[Session, Depends(get_db, scope="function")]
CurrentUser = Annotated[User, Depends(get_current_user)]


class Pagination:
    def __init__(
        self,
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> None:
        self.limit = limit
        self.offset = offset


def not_found(entity: str, identifier: int | int | str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"{entity} '{identifier}' was not found."
    )


def bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def conflict(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
