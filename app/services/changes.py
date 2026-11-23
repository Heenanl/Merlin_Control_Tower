from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional

from fastapi import HTTPException

from app.auth import CurrentUser
from app.db import StatementExecutionClient, comma_list, new_id, qualify, row_to_json, sql_ident, sql_literal, typed_literal, where_eq
from app.registry import TABLES, get_table
from app.services.dependencies import dependency_summary
from app.services.validation import validate_keys, validate_row, validate_share_sum
from app.settings import settings


def load_row(db: StatementExecutionClient, table_name: str, key: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    rows = db.execute(f"SELECT * FROM {qualify(table_name)} WHERE {where_eq(key)}", row_limit=1)
    return rows[0] if rows else None


def list_change_requests(db: StatementExecutionClient, *, limit: int = 200) -> List[Dict[str, Any]]:
    rows = db.execute(
        f"""
        SELECT request_id, title, object_type, object_id, status, drafted_by, approved_by, comment,
               created_at, submitted_at, decided_at, promoted_at, reverted_by, reverted_at, updated_at
        FROM {settings.fq_schema}.change_request
        ORDER BY coalesce(updated_at, created_at) DESC
        LIMIT {limit}
        """
    )
    return rows


def get_change_request(db: StatementExecutionClient, request_id: str) -> Dict[str, Any]:
    row = db.execute_one(f"SELECT * FROM {settings.fq_schema}.change_request WHERE request_id = {sql_literal(request_id)}")
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown change request: {request_id}")
    row["operations"] = json.loads(row["change_json"] or "[]")
    return row


def prepare_operations(db: StatementExecutionClient, operations: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    prepared: List[Dict[str, Any]] = []
    for op in operations:
        table_name = op["table"]
        spec = get_table(table_name)
        action = op["op"]
        key = dict(op.get("key") or {})
        after = deepcopy(op.get("after") or {})
        if key:
            validate_keys(table_name, key)
        if action == "insert":
            validate_row(table_name, after, for_create=True)
            key = {k: after[k] for k in spec.primary_keys}
            before = None
        else:
            before = deepcopy(op.get("before") or load_row(db, table_name, key) or {})
            if not before:
                raise HTTPException(status_code=404, detail=f"No current row found for {table_name}: {key}")
            if action == "update":
                # validate only the user-supplied changes; system columns come from the live row
                validate_row(table_name, after, for_create=False)
                after = {**before, **after}
            elif action == "soft_delete":
                after = dict(before)
                after[spec.soft_delete_column] = spec.soft_delete_value
            elif action == "hard_delete":
                after = None
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported operation: {action}")
        prepared.append(
            {
                "table": table_name,
                "op": action,
                "key": key,
                "before": before,
                "after": after,
                "dependencies": op.get("dependencies") or dependency_summary(db, table_name, key),
            }
        )
    validate_share_sum(prepared)
    return prepared


def create_change_request(
    db: StatementExecutionClient,
    user: CurrentUser,
    *,
    title: str,
    comment: str,
    object_type: str,
    object_id: str,
    operations: Iterable[Mapping[str, Any]],
    status: str = "draft",
    approved_by: str | None = None,
) -> Dict[str, Any]:
    prepared = prepare_operations(db, operations)
    request_id = new_id("cr")
    change_json = json.dumps(prepared, default=str)
    db.execute(
        f"""
        INSERT INTO {settings.fq_schema}.change_request (
          request_id, title, object_type, object_id, change_json, status, drafted_by, approved_by, comment,
          created_at, submitted_at, decided_at, promoted_at, reverted_by, reverted_at, updated_at,
          audit_timestamp, source_system
        ) VALUES (
          {sql_literal(request_id)}, {sql_literal(title)}, {sql_literal(object_type)}, {sql_literal(object_id)},
          {sql_literal(change_json)}, {sql_literal(status)}, {sql_literal(user.email)}, {sql_literal(approved_by)}, {sql_literal(comment)},
          current_timestamp(), {('current_timestamp()' if status in {'submitted','approved'} else 'NULL')},
          {('current_timestamp()' if status == 'approved' else 'NULL')}, NULL, NULL, NULL, current_timestamp(),
          current_timestamp(), {sql_literal(settings.source_system)}
        )
        """
    )
    db.execute(
        f"""
        INSERT INTO {settings.fq_schema}.change_audit_log (
          audit_id, request_id, action, actor, action_at, object_type, object_id, before_json, after_json, table_versions,
          audit_timestamp, source_system
        ) VALUES (
          {sql_literal(new_id('audit'))}, {sql_literal(request_id)}, {sql_literal(status if status != 'draft' else 'drafted')},
          {sql_literal(user.email)}, current_timestamp(), {sql_literal(object_type)}, {sql_literal(object_id)},
          NULL, {sql_literal(change_json)}, NULL, current_timestamp(), {sql_literal(settings.source_system)}
        )
        """
    )
    return get_change_request(db, request_id)


def submit_request(db: StatementExecutionClient, request_id: str, user: CurrentUser, comment: str | None = None) -> Dict[str, Any]:
    req = get_change_request(db, request_id)
    if req["status"] != "draft":
        raise HTTPException(status_code=400, detail="Only draft requests can be submitted")
    if req["drafted_by"].lower() != user.email.lower() and "approver" not in user.roles:
        raise HTTPException(status_code=403, detail="Only the drafter or an approver can submit this request")
    db.execute(
        f"""
        UPDATE {settings.fq_schema}.change_request
        SET status = 'submitted', comment = coalesce({sql_literal(comment)}, comment), submitted_at = current_timestamp(), updated_at = current_timestamp(),
            audit_timestamp = current_timestamp(), source_system = {sql_literal(settings.source_system)}
        WHERE request_id = {sql_literal(request_id)}
        """
    )
    db.execute(
        f"INSERT INTO {settings.fq_schema}.change_audit_log VALUES ({sql_literal(new_id('audit'))}, {sql_literal(request_id)}, 'submitted', {sql_literal(user.email)}, current_timestamp(), {sql_literal(req['object_type'])}, {sql_literal(req['object_id'])}, NULL, NULL, NULL, current_timestamp(), {sql_literal(settings.source_system)})"
    )
    return get_change_request(db, request_id)


def approve_request(db: StatementExecutionClient, request_id: str, user: CurrentUser, comment: str | None = None) -> Dict[str, Any]:
    user.require_any({"approver"})
    req = get_change_request(db, request_id)
    if req["drafted_by"].lower() == user.email.lower():
        raise HTTPException(status_code=403, detail="Drafters cannot approve their own requests")
    if req["status"] not in {"submitted", "draft"}:
        raise HTTPException(status_code=400, detail="Only draft or submitted requests can be approved")
    db.execute(
        f"""
        UPDATE {settings.fq_schema}.change_request
        SET status = 'approved', approved_by = {sql_literal(user.email)}, comment = coalesce({sql_literal(comment)}, comment),
            decided_at = current_timestamp(), updated_at = current_timestamp(),
            audit_timestamp = current_timestamp(), source_system = {sql_literal(settings.source_system)}
        WHERE request_id = {sql_literal(request_id)}
        """
    )
    db.execute(
        f"INSERT INTO {settings.fq_schema}.change_audit_log VALUES ({sql_literal(new_id('audit'))}, {sql_literal(request_id)}, 'approved', {sql_literal(user.email)}, current_timestamp(), {sql_literal(req['object_type'])}, {sql_literal(req['object_id'])}, NULL, NULL, NULL, current_timestamp(), {sql_literal(settings.source_system)})"
    )
    return get_change_request(db, request_id)


def reject_request(db: StatementExecutionClient, request_id: str, user: CurrentUser, comment: str) -> Dict[str, Any]:
    user.require_any({"approver"})
    req = get_change_request(db, request_id)
    db.execute(
        f"""
        UPDATE {settings.fq_schema}.change_request
        SET status = 'rejected', approved_by = {sql_literal(user.email)}, comment = {sql_literal(comment)},
            decided_at = current_timestamp(), updated_at = current_timestamp(),
            audit_timestamp = current_timestamp(), source_system = {sql_literal(settings.source_system)}
        WHERE request_id = {sql_literal(request_id)}
        """
    )
    db.execute(
        f"INSERT INTO {settings.fq_schema}.change_audit_log VALUES ({sql_literal(new_id('audit'))}, {sql_literal(request_id)}, 'rejected', {sql_literal(user.email)}, current_timestamp(), {sql_literal(req['object_type'])}, {sql_literal(req['object_id'])}, NULL, NULL, NULL, current_timestamp(), {sql_literal(settings.source_system)})"
    )
    return get_change_request(db, request_id)


def _metadata_assignments(actor_email: str, *, is_insert: bool) -> Dict[str, str]:
    assignments = {
        "updated_by": sql_literal(actor_email),
        "updated_at": "current_timestamp()",
        "audit_timestamp": "current_timestamp()",
        "source_system": sql_literal(settings.source_system),
    }
    if is_insert:
        assignments["created_by"] = sql_literal(actor_email)
        assignments["created_at"] = "current_timestamp()"
        assignments["version"] = "1"
    else:
        assignments["version"] = "coalesce(version, 0) + 1"
    return assignments


def _before_version_check(op: Mapping[str, Any]) -> str:
    before = op.get("before") or {}
    if not before or "version" not in before:
        return ""
    return f"""
    IF (SELECT count(*) FROM {qualify(op['table'])} WHERE {where_eq(op['key'])} AND version = {sql_literal(before['version'])}) <> 1 THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = {sql_literal(f"MERLIN_STALE: {op['table']} changed since draft")};
    END IF;
    """


def _write_sql(op: Mapping[str, Any], actor_email: str, col_types: Mapping[str, str]) -> str:
    """Build the DML for one operation. Business values are CAST to the real column type; system
    columns (created_*/updated_*/version/audit_timestamp/source_system) are always set server-side."""
    table_name = op["table"]
    action = op["op"]
    spec = get_table(table_name)
    key = op["key"]
    after = op.get("after") or {}

    if action == "insert":
        allowed = set(spec.create_columns)
        values = {c: typed_literal(v, col_types.get(c)) for c, v in after.items() if c in allowed}
        values.update(_metadata_assignments(actor_email, is_insert=True))
        values = {c: v for c, v in values.items() if c in col_types}
        cols = [sql_ident(c) for c in values]
        return f"INSERT INTO {qualify(table_name)} ({comma_list(cols)}) VALUES ({comma_list(values.values())})"

    if action in {"update", "soft_delete"}:
        allowed = set(spec.editable_columns) - set(spec.primary_keys)
        values = {c: typed_literal(v, col_types.get(c)) for c, v in after.items() if c in allowed}
        values.update(_metadata_assignments(actor_email, is_insert=False))
        values = {c: v for c, v in values.items() if c in col_types}
        set_sql = comma_list(f"{sql_ident(c)} = {v}" for c, v in values.items())
        return f"{_before_version_check(op)} UPDATE {qualify(table_name)} SET {set_sql} WHERE {where_eq(key)}"

    if action == "hard_delete":
        return f"{_before_version_check(op)} DELETE FROM {qualify(table_name)} WHERE {where_eq(key)}"

    raise HTTPException(status_code=400, detail=f"Unsupported operation: {action}")


def promote_request(db: StatementExecutionClient, request_id: str, user: CurrentUser) -> Dict[str, Any]:
    user.require_any({"approver"})
    req = get_change_request(db, request_id)
    if req["status"] != "approved":
        raise HTTPException(status_code=400, detail="Only approved requests can be promoted")
    if (req.get("approved_by") or "").lower() != user.email.lower():
        raise HTTPException(status_code=403, detail="Only the approver who approved this request can promote it")

    statements = [
        f"IF (SELECT count(*) FROM {settings.fq_schema}.change_request WHERE request_id = {sql_literal(request_id)} AND status = 'approved') <> 1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'MERLIN_INVALID_STATUS'; END IF;"
    ]
    audit_rows = []
    type_cache: Dict[str, Dict[str, str]] = {}
    for op in req["operations"]:
        if op["table"] not in type_cache:
            type_cache[op["table"]] = db.column_types(op["table"])
        statements.append(_write_sql(op, user.email, type_cache[op["table"]]) + ";")
        audit_rows.append(
            f"({sql_literal(new_id('audit'))}, {sql_literal(request_id)}, 'promoted', {sql_literal(user.email)}, current_timestamp(), {sql_literal(op['table'])}, {row_to_json(op['key'])}, {row_to_json(op.get('before') or {})}, {row_to_json(op.get('after') or {})}, NULL, current_timestamp(), {sql_literal(settings.source_system)})"
        )

    statements.append(
        f"UPDATE {settings.fq_schema}.change_request SET status = 'promoted', promoted_at = current_timestamp(), updated_at = current_timestamp(), audit_timestamp = current_timestamp(), source_system = {sql_literal(settings.source_system)} WHERE request_id = {sql_literal(request_id)};"
    )
    if audit_rows:
        statements.append(
            f"INSERT INTO {settings.fq_schema}.change_audit_log (audit_id, request_id, action, actor, action_at, object_type, object_id, before_json, after_json, table_versions, audit_timestamp, source_system) VALUES {comma_list(audit_rows)};"
        )
    sql = "BEGIN ATOMIC\n  " + "\n  ".join(statements) + "\nEND;"
    db.execute(sql, wait_s=60)
    return get_change_request(db, request_id)


def revert_request(db: StatementExecutionClient, request_id: str, user: CurrentUser) -> Dict[str, Any]:
    user.require_any({"approver"})
    req = get_change_request(db, request_id)
    if req["status"] != "promoted":
        raise HTTPException(status_code=400, detail="Only promoted requests can be reverted")

    reverse_ops = []
    for op in reversed(req["operations"]):
        reverse_ops.append(
            {
                "table": op["table"],
                "op": "hard_delete" if op["op"] == "insert" else ("insert" if op["op"] == "hard_delete" else "update"),
                "key": op["key"],
                # before=None -> prepare_operations reloads the current live row (with its current version)
                "before": None,
                "after": op.get("before"),
            }
        )
    revert_cr = create_change_request(
        db,
        user,
        title=f"Revert {req['title']}",
        comment=f"Revert of {request_id}",
        object_type=req["object_type"],
        object_id=req["object_id"],
        operations=reverse_ops,
        status="approved",
        approved_by=user.email,
    )
    promote_request(db, revert_cr["request_id"], user)
    db.execute(
        f"UPDATE {settings.fq_schema}.change_request SET status = 'reverted', reverted_by = {sql_literal(user.email)}, reverted_at = current_timestamp(), updated_at = current_timestamp(), audit_timestamp = current_timestamp(), source_system = {sql_literal(settings.source_system)} WHERE request_id = {sql_literal(request_id)}"
    )
    return get_change_request(db, revert_cr["request_id"])
