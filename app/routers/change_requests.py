from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from app.auth import CurrentUser
from app.services.changes import (
    approve_request,
    get_change_request,
    list_change_requests,
    promote_request,
    reject_request,
    revert_request,
    submit_request,
)
from app.services.dependencies import current_user_dep, db_for_user
from app.services.history import request_history
from app.services.impact import preview_change_impact

router = APIRouter(prefix="/api/change-requests", tags=["change-requests"])


@router.get("")
async def list_requests(user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    return {"rows": list_change_requests(db)}


@router.get("/{request_id}")
async def get_request(request_id: str, user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    return get_change_request(db, request_id)


@router.get("/{request_id}/preview")
async def preview(request_id: str, user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    req = get_change_request(db, request_id)
    return preview_change_impact(db, req["operations"])


@router.get("/{request_id}/history")
async def history(request_id: str, user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    return {"rows": request_history(db, request_id)}


@router.post("/{request_id}/submit")
async def submit(request_id: str, payload: dict = Body(default={}), user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    return submit_request(db, request_id, user, payload.get("comment"))


@router.post("/{request_id}/approve")
async def approve(request_id: str, payload: dict = Body(default={}), user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    return approve_request(db, request_id, user, payload.get("comment"))


@router.post("/{request_id}/reject")
async def reject(request_id: str, payload: dict = Body(default={}), user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    return reject_request(db, request_id, user, payload.get("comment") or "Rejected")


@router.post("/{request_id}/promote")
async def promote(request_id: str, user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    return promote_request(db, request_id, user)


@router.post("/{request_id}/revert")
async def revert(request_id: str, user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    return revert_request(db, request_id, user)
