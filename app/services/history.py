from __future__ import annotations

from typing import Any, Dict, Mapping

from app.db import StatementExecutionClient, sql_literal
from app.settings import settings


def record_history(db: StatementExecutionClient, object_type: str, object_id: str) -> list[dict[str, Any]]:
    return db.execute(
        f"""
        SELECT request_id, action, actor, action_at, before_json, after_json, table_versions
        FROM {settings.fq_schema}.change_audit_log
        WHERE object_type = {sql_literal(object_type)} AND object_id = {sql_literal(object_id)}
        ORDER BY action_at DESC
        """
    )


def request_history(db: StatementExecutionClient, request_id: str) -> list[dict[str, Any]]:
    return db.execute(
        f"""
        SELECT request_id, action, actor, action_at, object_type, object_id, before_json, after_json, table_versions
        FROM {settings.fq_schema}.change_audit_log
        WHERE request_id = {sql_literal(request_id)}
        ORDER BY action_at DESC
        """
    )
