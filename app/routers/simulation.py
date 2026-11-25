from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import CurrentUser
from app.services.dependencies import current_user_dep, db_for_user
from app.services.simulation import run_simulation, simulation_summary, start_simulation, poll_simulation_status

router = APIRouter(prefix="/api/simulation", tags=["simulation"])


@router.get("/summary")
def summary(user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    return simulation_summary(db)


@router.post("/run")
def run(user: CurrentUser = Depends(current_user_dep)) -> dict:
    """Submit simulation SQL asynchronously; returns statement_id immediately."""
    user.require_any({"approver"})
    db = db_for_user(user)
    return start_simulation(db)


@router.get("/status/{statement_id}")
def status(statement_id: str, user: CurrentUser = Depends(current_user_dep)) -> dict:
    """Poll the status of a running simulation; returns summary when SUCCEEDED."""
    db = db_for_user(user)
    return poll_simulation_status(db, statement_id)
