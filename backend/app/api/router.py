"""API router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import ai, auth, resources, scenarios, scheduling, system, work_orders

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(work_orders.router)
api_router.include_router(resources.router)
api_router.include_router(scheduling.router)
api_router.include_router(scenarios.router)
api_router.include_router(ai.router)
api_router.include_router(system.router)
