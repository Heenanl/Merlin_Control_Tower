from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.auth import CurrentUser
from app.db import sql_literal
from app.registry import RULE_MATRIX_SQL, get_table, table_names
from app.services.changes import create_change_request, load_row
from app.services.dependencies import current_user_dep, db_for_user
from app.services.history import record_history
from app.settings import settings

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config/tables")
async def config_tables(user: CurrentUser = Depends(current_user_dep)) -> dict:
    return {"tables": sorted(table_names())}


@router.get("/config/{table_name}")
async def config_rows(
    table_name: str,
    limit: int = Query(500, ge=1, le=5000),
    user: CurrentUser = Depends(current_user_dep),
) -> dict:
    spec = get_table(table_name)
    db = db_for_user(user)
    rows = db.execute(f"SELECT * FROM {settings.fq_schema}.{table_name} ORDER BY {', '.join(spec.primary_keys)} LIMIT {limit}", row_limit=limit)
    return {"table": table_name, "rows": rows}


@router.post("/config/{table_name}/draft")
async def draft_table_change(
    table_name: str,
    payload: dict = Body(...),
    user: CurrentUser = Depends(current_user_dep),
) -> dict:
    user.require_any({"drafter", "approver"})
    db = db_for_user(user)
    action = payload["op"]
    key = payload.get("key") or {}
    before = load_row(db, table_name, key) if key else None
    change_request = create_change_request(
        db,
        user,
        title=payload.get("title") or f"{action.title()} {table_name}",
        comment=payload.get("comment") or "",
        object_type=table_name,
        object_id=str(key or payload.get("after") or "multi"),
        operations=[{
            "table": table_name,
            "op": action,
            "key": key,
            "before": before,
            "after": payload.get("after"),
        }],
    )
    return change_request


@router.post("/config/{table_name}/bulk-draft")
async def draft_bulk_table_change(
    table_name: str,
    payload: dict = Body(...),
    user: CurrentUser = Depends(current_user_dep),
) -> dict:
    user.require_any({"drafter", "approver"})
    db = db_for_user(user)
    operations = payload.get("operations") or []
    if not operations:
        raise HTTPException(status_code=400, detail="No operations supplied")
    for op in operations:
        op["table"] = table_name
        if op.get("key"):
            op["before"] = load_row(db, table_name, op["key"])
    return create_change_request(
        db,
        user,
        title=payload.get("title") or f"Bulk change: {table_name}",
        comment=payload.get("comment") or "",
        object_type="multi",
        object_id=table_name,
        operations=operations,
    )


@router.get("/matrix")
async def rule_content_matrix(user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    rows = db.execute(RULE_MATRIX_SQL, wait_s=60, row_limit=10_000)
    return {"rows": rows}


@router.post("/matrix/toggle")
async def toggle_matrix_mapping(payload: dict = Body(...), user: CurrentUser = Depends(current_user_dep)) -> dict:
    user.require_any({"drafter", "approver"})
    db = db_for_user(user)
    key = {"content_id": payload["content_id"], "eligibility_rule_id": payload["eligibility_rule_id"]}
    current = load_row(db, "content_eligibility_rule", key)
    if current and current.get("active_ind", True):
        op = {"table": "content_eligibility_rule", "op": "soft_delete", "key": key, "before": current}
        title = f"Remove rule {payload['eligibility_rule_id']} from {payload['content_id']}"
    elif current:
        after = dict(current)
        after["active_ind"] = True
        op = {"table": "content_eligibility_rule", "op": "update", "key": key, "before": current, "after": after}
        title = f"Restore rule {payload['eligibility_rule_id']} to {payload['content_id']}"
    else:
        after = {**key, "eligibility_rule_set_id": payload.get("eligibility_rule_set_id"), "active_ind": True}
        op = {"table": "content_eligibility_rule", "op": "insert", "key": key, "after": after}
        title = f"Add rule {payload['eligibility_rule_id']} to {payload['content_id']}"
    return create_change_request(db, user, title=title, comment=payload.get("comment") or "", object_type="content_eligibility_rule", object_id=str(key), operations=[op])


@router.post("/rules/proposition-wizard")
async def proposition_wizard(payload: dict = Body(...), user: CurrentUser = Depends(current_user_dep)) -> dict:
    user.require_any({"drafter", "approver"})
    db = db_for_user(user)
    max_rule = int(db.execute_scalar(f"SELECT coalesce(max(eligibility_rule_id), 1000) FROM {settings.fq_schema}.eligibility_rule WHERE eligibility_rule_id < 10000", default=1000))
    proposition_id = payload["proposition_id"]
    content_id = payload["content_id"]
    exclusion_rule_id = max_rule + 1
    operations = [
        {
            "table": "proposition",
            "op": "insert",
            "key": {"proposition_id": proposition_id},
            "after": {
                "proposition_id": proposition_id,
                "name": payload["name"],
                "product_family": payload["product_family"],
                "proposition_weight": payload.get("proposition_weight", 1.0),
                "active_ind": True,
            },
        },
        {
            "table": "content",
            "op": "insert",
            "key": {"content_id": content_id},
            "after": {
                "content_id": content_id,
                "message_type": payload.get("message_type", "offer"),
                "proposition_id": proposition_id,
                "channel_code": payload.get("channel_code", "email"),
                "optin_id": payload.get("optin_id", "OPT_EMAIL_MARKETING"),
                "language_code": payload.get("language_code", "NL"),
                "customer_segment_id": payload.get("customer_segment_id", "ALL"),
                "content_frequency": payload.get("content_frequency", 1),
                "content_frequency_level": payload.get("content_frequency_level", "monthly"),
                "active_content_ind": True,
                "last_updated_date": payload.get("last_updated_date"),
            },
        },
        {
            "table": "eligibility_rule",
            "op": "insert",
            "key": {"eligibility_rule_id": exclusion_rule_id},
            "after": {
                "eligibility_rule_id": exclusion_rule_id,
                "eligibility_rule_level": "proposition",
                "eligibility_rule": f"Exclude customers who already own {payload['name']}",
                "eligibility_rule_type": "Exclude",
                "sql_condition": f"array_contains(owned_products, '{proposition_id}')",
                "status": "live",
            },
        },
        {
            "table": "content_eligibility_rule",
            "op": "insert",
            "key": {"content_id": content_id, "eligibility_rule_id": exclusion_rule_id},
            "after": {
                "content_id": content_id,
                "eligibility_rule_id": exclusion_rule_id,
                "eligibility_rule_set_id": None,
                "active_ind": True,
            },
        },
    ]
    return create_change_request(db, user, title=f"New proposition: {payload['name']}", comment=payload.get("comment") or "", object_type="multi", object_id=proposition_id, operations=operations)


@router.get("/history/{object_type}/{object_id}")
async def history(object_type: str, object_id: str, user: CurrentUser = Depends(current_user_dep)) -> dict:
    db = db_for_user(user)
    return {"rows": record_history(db, object_type, object_id)}
