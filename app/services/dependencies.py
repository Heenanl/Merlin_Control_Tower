from __future__ import annotations

from typing import Any, Dict, Mapping

from fastapi import Depends

from app.auth import CurrentUser, forwarded_identity
from app.db import StatementExecutionClient, sql_literal
from app.settings import settings


async def current_user_dep(user: CurrentUser = Depends(forwarded_identity)) -> CurrentUser:
    return user


def db_for_user(user: CurrentUser) -> StatementExecutionClient:
    return StatementExecutionClient(user.access_token)


def dependency_summary(db: StatementExecutionClient, table_name: str, key: Mapping[str, Any]) -> Dict[str, Any]:
    if table_name == "eligibility_rule":
        rid = sql_literal(key["eligibility_rule_id"])
        return {
            "mapped_content": db.execute_scalar(
                f"SELECT count(*) FROM {settings.fq_schema}.content_eligibility_rule WHERE eligibility_rule_id = {rid}",
                default=0,
            ),
            "rule_sets": db.execute_scalar(
                f"SELECT count(*) FROM {settings.fq_schema}.eligibility_rule_set WHERE eligibility_rule_id = {rid}",
                default=0,
            ),
        }
    if table_name == "proposition":
        pid = sql_literal(key["proposition_id"])
        return {
            "content_rows": db.execute_scalar(
                f"SELECT count(*) FROM {settings.fq_schema}.content WHERE proposition_id = {pid}",
                default=0,
            ),
            "recent_contact_rule": db.execute_scalar(
                f"SELECT count(*) FROM {settings.fq_schema}.eligibility_rule WHERE eligibility_rule_id = 10000 + cast(substr({pid}, 2, 3) AS INT)",
                default=0,
            ),
        }
    if table_name == "content":
        cid = sql_literal(key["content_id"])
        return {
            "rule_mappings": db.execute_scalar(
                f"SELECT count(*) FROM {settings.fq_schema}.content_eligibility_rule WHERE content_id = {cid}",
                default=0,
            ),
            "prior_contacts": db.execute_scalar(
                f"SELECT count(*) FROM {settings.fq_schema}.contact_decision_output WHERE content_id = {cid}",
                default=0,
            ),
        }
    if table_name == "model_metadata":
        return {"depends_on": "impact preview and simulation shares, thresholds and caps"}
    return {}
