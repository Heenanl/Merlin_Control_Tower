from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import CurrentUser
from app.services.dependencies import current_user_dep, db_for_user
from app.services.simulation import run_simulation, simulation_summary

router = APIRouter(prefix="/api/simulation", tags=["simulation"])


@router.get("/summary")
async def summary(user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    return simulation_summary(db)


@router.post("/run")
async def run(user: CurrentUser = Depends(current_user_dep)) -> dict:
    user.require_any({"approver"})
    db = db_for_user(user)
    return run_simulation(db)
