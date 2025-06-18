"""FastAPI application entry point."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.router import api_router
from app.config import settings
from app.db import Base, SessionLocal, engine
from app.logging_config import configure_logging, log
from app.services.auth import ensure_bootstrap_admin

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("app.startup", **settings.safe_summary())
    if settings.is_sqlite:
        Base.metadata.create_all(engine)
    with SessionLocal() as session:
        ensure_bootstrap_admin(session)
        session.commit()
    yield
    log.info("app.shutdown")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Constraint-aware production scheduling platform. OR-Tools CP-SAT produces the "
        "schedules; the AI layer only explains validated solver output."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple per-client rate limiter protecting expensive endpoints."""

    def __init__(self, app: FastAPI) -> None:
        super().__init__(app)
        self._hits: dict[str, list[float]] = {}

    async def dispatch(self, request: Request, call_next):
        if not settings.rate_limit_enabled:
            return await call_next(request)
        client = request.client.host if request.client else "unknown"
        now = time.time()
        window = settings.rate_limit_window_seconds
        hits = [t for t in self._hits.get(client, []) if now - t < window]
        if len(hits) >= settings.rate_limit_requests:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests — please slow down and try again shortly."},
            )
        hits.append(now)
        self._hits[client] = hits
        return await call_next(request)


app.add_middleware(RateLimitMiddleware)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    duration = (time.perf_counter() - started) * 1000
    log.info(
        "http.request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(duration, 2),
    )
    return response


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    """Never leak a stack trace to the client — log it and return clean JSON."""
    log.error(
        "app.unhandled_error",
        path=request.url.path,
        method=request.method,
        error=str(exc),
        error_type=type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected server error occurred. Check the server logs."},
    )


app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
def root() -> dict[str, Any]:
    return {
        "name": settings.app_name,
        "version": "1.0.0",
        "docs": "/docs",
        "api": settings.api_prefix,
    }
