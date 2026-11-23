from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping

from app.db import StatementExecutionClient, comma_list, qualify, sql_literal
from app.settings import settings

CONFIG_TABLES = ["proposition", "content", "eligibility_rule", "content_eligibility_rule", "model_metadata"]


def load_config_state(db: StatementExecutionClient) -> Dict[str, List[Dict[str, Any]]]:
    state: Dict[str, List[Dict[str, Any]]] = {}
    for table_name in CONFIG_TABLES:
        state[table_name] = db.execute(f"SELECT * FROM {qualify(table_name)}", row_limit=20_000)
    return state


def apply_ops_to_state(state: Dict[str, List[Dict[str, Any]]], operations: Iterable[Mapping[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    result = deepcopy(state)
    for op in operations:
        table_name = op["table"]
        rows = result[table_name]
        key = op["key"]
        if table_name == "model_metadata":
            matcher = lambda row: row["key"] == key["key"]
        elif table_name == "proposition":
            matcher = lambda row: row["proposition_id"] == key["proposition_id"]
        elif table_name == "content":
            matcher = lambda row: row["content_id"] == key["content_id"]
        elif table_name == "eligibility_rule":
            matcher = lambda row: int(row["eligibility_rule_id"]) == int(key["eligibility_rule_id"])
        else:
            matcher = lambda row: row["content_id"] == key["content_id"] and int(row["eligibility_rule_id"]) == int(key["eligibility_rule_id"])
        idx = next((i for i, row in enumerate(rows) if matcher(row)), None)
        if op["op"] == "insert":
            rows.append(deepcopy(op["after"]))
        elif op["op"] in {"update", "soft_delete"}:
            rows[idx] = deepcopy(op["after"])
        elif op["op"] == "hard_delete" and idx is not None:
            del rows[idx]
    return result


def metadata_map(rows: Iterable[Mapping[str, Any]]) -> Dict[str, str]:
    return {str(row["key"]): str(row["value"]) for row in rows}


def _condition_text(sql_condition: str, metadata: Mapping[str, str]) -> str:
    text = sql_condition or "true"
    text = text.replace("{followup_after_days}", metadata.get("followup_after_days", str(settings.default_followup_days)))
    return text


def _rule_union_sql(state: Dict[str, List[Dict[str, Any]]]) -> str:
    propositions = {row["proposition_id"]: row for row in state["proposition"] if row.get("active_ind", True)}
    content = {row["content_id"]: row for row in state["content"] if row.get("active_content_ind", True)}
    rules = {int(row["eligibility_rule_id"]): row for row in state["eligibility_rule"] if row.get("status") == "live"}
    metadata = metadata_map(state["model_metadata"])
    selects = []
    for mapping in state["content_eligibility_rule"]:
        if not mapping.get("active_ind", True):
            continue
        content_row = content.get(mapping["content_id"])
        rule_row = rules.get(int(mapping["eligibility_rule_id"]))
        if not content_row or not rule_row:
            continue
        if content_row["proposition_id"] not in propositions:
            continue
        predicate = _condition_text(rule_row["sql_condition"], metadata)
        selects.append(
            f"SELECT cust.business_contact_number, {sql_literal(content_row['proposition_id'])} AS proposition_id, {sql_literal(content_row['content_id'])} AS content_id, {sql_literal(rule_row['eligibility_rule_type'])} AS rule_type, CASE WHEN {predicate} THEN 1 ELSE 0 END AS matched FROM {settings.fq_schema}.customer cust"
        )
    if not selects:
        return f"SELECT cast(NULL AS STRING) AS business_contact_number, cast(NULL AS STRING) AS proposition_id, cast(NULL AS STRING) AS content_id, cast(NULL AS STRING) AS rule_type, cast(NULL AS INT) AS matched WHERE 1 = 0"
    return "\nUNION ALL\n".join(selects)


def _eligible_count_sql(state: Dict[str, List[Dict[str, Any]]], alias: str) -> str:
    union_sql = _rule_union_sql(state)
    return f"""
    {alias}_rule_rows AS (
      {union_sql}
    ),
    {alias}_eligible_content AS (
      SELECT business_contact_number, proposition_id, content_id,
             min(CASE WHEN rule_type = 'Include' THEN matched ELSE 1 END) AS include_ok,
             max(CASE WHEN rule_type = 'Exclude' THEN matched ELSE 0 END) AS exclude_hit
      FROM {alias}_rule_rows
      GROUP BY business_contact_number, proposition_id, content_id
    ),
    {alias}_eligible_prop AS (
      SELECT proposition_id, count(DISTINCT business_contact_number) AS eligible_customers
      FROM {alias}_eligible_content
      WHERE include_ok = 1 AND exclude_hit = 0
      GROUP BY proposition_id
    )
    """


def preview_change_impact(db: StatementExecutionClient, operations: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    before = load_config_state(db)
    after = apply_ops_to_state(before, operations)
    current_meta = metadata_map(before["model_metadata"])
    proposed_meta = metadata_map(after["model_metadata"])
    customer_count = db.execute_scalar(f"SELECT count(*) FROM {settings.fq_schema}.customer", default=0)

    scaled_before = int(float(current_meta.get("weekly_cap", 0)) * customer_count / float(current_meta.get("reference_population", settings.reference_population)))
    scaled_after = int(float(proposed_meta.get("weekly_cap", 0)) * customer_count / float(proposed_meta.get("reference_population", settings.reference_population)))

    sql = f"""
    WITH
    {_eligible_count_sql(before, 'before')},
    {_eligible_count_sql(after, 'after')}
    SELECT
      coalesce(b.proposition_id, a.proposition_id) AS proposition_id,
      coalesce(b.eligible_customers, 0) AS eligible_before,
      coalesce(a.eligible_customers, 0) AS eligible_after,
      coalesce(a.eligible_customers, 0) - coalesce(b.eligible_customers, 0) AS delta
    FROM before_eligible_prop b
    FULL OUTER JOIN after_eligible_prop a USING (proposition_id)
    ORDER BY proposition_id
    """
    rows = db.execute(sql, wait_s=60, row_limit=200)
    total_before = sum(int(r["eligible_before"] or 0) for r in rows) or 1
    total_after = sum(int(r["eligible_after"] or 0) for r in rows) or 1
    exploit_before = float(current_meta.get("exploit_share", 0.75))
    explore_before = float(current_meta.get("explore_share", 0.25))
    exploit_after = float(proposed_meta.get("exploit_share", 0.75))
    explore_after = float(proposed_meta.get("explore_share", 0.25))

    for row in rows:
        before_share = (row["eligible_before"] or 0) / total_before
        after_share = (row["eligible_after"] or 0) / total_after
        row["estimated_contacts_before"] = int(scaled_before * before_share)
        row["estimated_exploit_before"] = int(row["estimated_contacts_before"] * exploit_before)
        row["estimated_explore_before"] = int(row["estimated_contacts_before"] * explore_before)
        row["estimated_contacts_after"] = int(scaled_after * after_share)
        row["estimated_exploit_after"] = int(row["estimated_contacts_after"] * exploit_after)
        row["estimated_explore_after"] = int(row["estimated_contacts_after"] * explore_after)

    return {
        "customer_count": customer_count,
        "scaled_weekly_cap_before": scaled_before,
        "scaled_weekly_cap_after": scaled_after,
        "holdout_share_before": float(current_meta.get("holdout_share", 0.10)),
        "holdout_share_after": float(proposed_meta.get("holdout_share", 0.10)),
        "rows": rows,
    }
