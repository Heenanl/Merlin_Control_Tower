from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import CurrentUser
from app.db import StatementExecutionClient
from app.registry import OVERVIEW_SQL
from app.services.dependencies import current_user_dep, db_for_user

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/me")
async def me(user: CurrentUser = Depends(current_user_dep)) -> dict:
    return {
        "email": user.email,
        "preferred_username": user.preferred_username,
        "roles": sorted(user.roles),
    }


@router.get("/overview")
async def overview(user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    summary = db.execute_one(OVERVIEW_SQL) or {}
    return summary
