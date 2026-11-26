from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from fastapi import HTTPException

from app.settings import settings


@dataclass(frozen=True)
class TableSpec:
    name: str
    primary_keys: List[str]
    editable_columns: List[str]
    create_columns: List[str]
    soft_delete_column: Optional[str] = None
    soft_delete_value: Any = False
    hard_delete_allowed: bool = False
    history_label: str = ""


TABLES: Dict[str, TableSpec] = {
    "proposition": TableSpec(
        name="proposition",
        primary_keys=["proposition_id"],
        editable_columns=["name", "product_family", "proposition_weight", "active_ind"],
        create_columns=["proposition_id", "name", "product_family", "proposition_weight", "active_ind"],
        soft_delete_column="active_ind",
        soft_delete_value=False,
        hard_delete_allowed=True,
        history_label="Proposition",
    ),
    "content": TableSpec(
        name="content",
        primary_keys=["content_id"],
        editable_columns=[
            "message_type",
            "proposition_id",
            "channel_code",
            "optin_id",
            "language_code",
            "customer_segment_id",
            "content_frequency",
            "content_frequency_level",
            "active_content_ind",
            "last_updated_date",
        ],
        create_columns=[
            "content_id",
            "message_type",
            "proposition_id",
            "channel_code",
            "optin_id",
            "language_code",
            "customer_segment_id",
            "content_frequency",
            "content_frequency_level",
            "active_content_ind",
            "last_updated_date",
        ],
        soft_delete_column="active_content_ind",
        soft_delete_value=False,
        hard_delete_allowed=True,
        history_label="Content",
    ),
    "eligibility_rule": TableSpec(
        name="eligibility_rule",
        primary_keys=["eligibility_rule_id"],
        editable_columns=["eligibility_rule_level", "eligibility_rule", "eligibility_rule_type", "sql_condition", "status"],
        create_columns=["eligibility_rule_id", "eligibility_rule_level", "eligibility_rule", "eligibility_rule_type", "sql_condition", "status"],
        soft_delete_column="status",
        soft_delete_value="retired",
        hard_delete_allowed=True,
        history_label="Eligibility rule",
    ),
    "eligibility_rule_set": TableSpec(
        name="eligibility_rule_set",
        primary_keys=["eligibility_rule_set_id", "eligibility_rule_id"],
        editable_columns=["rule_set_name", "active_ind"],
        create_columns=["eligibility_rule_set_id", "rule_set_name", "eligibility_rule_id", "active_ind"],
        soft_delete_column="active_ind",
        soft_delete_value=False,
        hard_delete_allowed=True,
        history_label="Rule set membership",
    ),
    "content_eligibility_rule": TableSpec(
        name="content_eligibility_rule",
        primary_keys=["content_id", "eligibility_rule_id"],
        editable_columns=["eligibility_rule_set_id", "active_ind"],
        create_columns=["content_id", "eligibility_rule_id", "eligibility_rule_set_id", "active_ind"],
        soft_delete_column="active_ind",
        soft_delete_value=False,
        hard_delete_allowed=True,
        history_label="Content-rule mapping",
    ),
    "model_metadata": TableSpec(
        name="model_metadata",
        primary_keys=["key"],
        editable_columns=["value", "value_type", "description"],
        create_columns=["key", "value", "value_type", "description"],
        hard_delete_allowed=True,
        history_label="Model metadata",
    ),
}

RULE_MATRIX_SQL = f"""
SELECT
  c.content_id,
  c.proposition_id,
  p.name AS proposition_name,
  c.language_code,
  c.channel_code,
  c.message_type,
  c.customer_segment_id,
  c.active_content_ind,
  r.eligibility_rule_id,
  r.eligibility_rule_level,
  r.eligibility_rule,
  r.eligibility_rule_type,
  r.status,
  CASE WHEN cer.content_id IS NULL OR coalesce(cer.active_ind, true) = false THEN false ELSE true END AS mapped,
  cer.eligibility_rule_set_id,
  c.updated_at AS content_updated_at,
  r.updated_at AS rule_updated_at
FROM {settings.fq_schema}.content c
LEFT JOIN {settings.fq_schema}.proposition p ON p.proposition_id = c.proposition_id
CROSS JOIN {settings.fq_schema}.eligibility_rule r
LEFT JOIN {settings.fq_schema}.content_eligibility_rule cer
  ON cer.content_id = c.content_id AND cer.eligibility_rule_id = r.eligibility_rule_id
WHERE c.active_content_ind = true
ORDER BY c.proposition_id, c.content_id, r.eligibility_rule_id
"""

OVERVIEW_SQL = f"""
WITH latest_sim AS (
  SELECT max(run_timestamp) AS last_simulation_run, max(snapshotdate) AS last_snapshotdate
  FROM {settings.fq_schema}.contact_decision_output
),
stale_content AS (
  SELECT round(100.0 * avg(CASE WHEN datediff(current_date(), last_updated_date) > {settings.stale_content_days} THEN 1 ELSE 0 END), 2) AS pct_stale
  FROM {settings.fq_schema}.content
)
SELECT
  (SELECT count(*) FROM {settings.fq_schema}.eligibility_rule WHERE status = 'live') AS live_rules,
  (SELECT count(*) FROM {settings.fq_schema}.content WHERE active_content_ind = true) AS active_content,
  (SELECT pct_stale FROM stale_content) AS pct_content_older_than_300_days,
  (SELECT count(*) FROM {settings.fq_schema}.change_request WHERE status IN ('draft','submitted','approved')) AS open_change_requests,
  (SELECT last_simulation_run FROM latest_sim) AS last_simulation_run,
  (SELECT last_snapshotdate FROM latest_sim) AS last_snapshotdate
"""


def get_table(table_name: str) -> TableSpec:
    if table_name not in TABLES:
        raise HTTPException(status_code=400, detail=f"Unsupported table: {table_name}")
    return TABLES[table_name]


def table_names() -> Iterable[str]:
    return TABLES.keys()
