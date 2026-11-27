from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import CurrentUser
from app.db import StatementExecutionClient
from app.registry import overview_sql
from app.services.dependencies import current_user_dep, db_for_user
from app.settings import settings

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/me")
def me(user: CurrentUser = Depends(current_user_dep)) -> dict:
    return {
        "email": user.email,
        "preferred_username": user.preferred_username,
        "roles": sorted(user.roles),
        "self_approval_enabled": settings.allow_self_approval,
        "backend": user.backend,
        "lakebase_available": bool(settings.pg_host and settings.lakebase_endpoint),
        "default_backend": settings.default_backend,
    }


@router.get("/overview")
def overview(user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    summary = db.execute_one(overview_sql(db.backend)) or {}
    return summary
