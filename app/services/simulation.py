from __future__ import annotations

from typing import Any, Dict, List

from app.db import StatementExecutionClient, new_id, sql_literal
from app.services.impact import _rule_union_sql, load_config_state, metadata_map
from app.settings import settings


def _candidate_cte_sql(db: StatementExecutionClient) -> str:
    state = load_config_state(db)
    rule_union = _rule_union_sql(state)
    meta = metadata_map(state["model_metadata"])
    threshold = float(meta.get("score_threshold", 0.30))
    return f"""
    WITH rule_rows AS (
      {rule_union}
    ),
    eligible_content AS (
      SELECT business_contact_number, proposition_id, content_id,
             min(CASE WHEN rule_type = 'Include' THEN matched ELSE 1 END) AS include_ok,
             max(CASE WHEN rule_type = 'Exclude' THEN matched ELSE 0 END) AS exclude_hit
      FROM rule_rows
      GROUP BY business_contact_number, proposition_id, content_id
    ),
    proposition_content AS (
      SELECT ec.business_contact_number, ec.proposition_id, ec.content_id, c.channel_code, c.last_updated_date,
             ms.model_score, p.proposition_weight,
             round(ms.model_score * p.proposition_weight, 6) AS weighted_score,
             row_number() OVER (
               PARTITION BY ec.business_contact_number, ec.proposition_id
               ORDER BY c.last_updated_date DESC, ec.content_id
             ) AS proposition_content_rank
      FROM eligible_content ec
      JOIN {settings.fq_schema}.content c ON c.content_id = ec.content_id
      JOIN {settings.fq_schema}.proposition p ON p.proposition_id = ec.proposition_id
      JOIN {settings.fq_schema}.model_score ms ON ms.business_contact_number = ec.business_contact_number AND ms.proposition_id = ec.proposition_id
      WHERE ec.include_ok = 1 AND ec.exclude_hit = 0 AND c.active_content_ind = true AND p.active_ind = true
    ),
    candidate_rows AS (
      SELECT *, row_number() OVER (PARTITION BY business_contact_number ORDER BY weighted_score DESC, proposition_id) AS overall_rank
      FROM proposition_content
      WHERE proposition_content_rank = 1 AND weighted_score >= {threshold}
    )
    """


def run_simulation(db: StatementExecutionClient) -> Dict[str, Any]:
    state = load_config_state(db)
    meta = metadata_map(state["model_metadata"])
    scaled_cap = int(float(meta.get("weekly_cap", 0)) * db.execute_scalar(f"SELECT count(*) FROM {settings.fq_schema}.customer", default=0) / float(meta.get("reference_population", settings.reference_population)))
    exploit_share = float(meta.get("exploit_share", 0.75))
    explore_share = float(meta.get("explore_share", 0.25))
    holdout_share = float(meta.get("holdout_share", 0.10))
    exploit_cap = int(scaled_cap * exploit_share)
    explore_cap = int(scaled_cap * explore_share)
    run_id = new_id("sim")

    cte = _candidate_cte_sql(db)
    nbe_insert = f"""
    INSERT INTO {settings.fq_schema}.next_best_experience
    {cte},
    holdout_customers AS (
      SELECT DISTINCT business_contact_number
      FROM candidate_rows
      WHERE rand(17) < {holdout_share}
    ),
    exploit_base AS (
      SELECT * FROM candidate_rows WHERE overall_rank = 1 AND business_contact_number NOT IN (SELECT business_contact_number FROM holdout_customers)
    ),
    explore_base AS (
      SELECT * FROM candidate_rows WHERE overall_rank > 1 AND business_contact_number NOT IN (SELECT business_contact_number FROM holdout_customers)
      QUALIFY row_number() OVER (PARTITION BY business_contact_number ORDER BY rand(29)) = 1
    ),
    exploit_selected AS (
      SELECT * FROM exploit_base ORDER BY weighted_score DESC LIMIT {exploit_cap}
    ),
    explore_selected AS (
      SELECT * FROM explore_base WHERE business_contact_number NOT IN (SELECT business_contact_number FROM exploit_selected)
      ORDER BY rand(41) LIMIT {explore_cap}
    ),
    holdout_selected AS (
      SELECT * FROM candidate_rows WHERE overall_rank = 1 AND business_contact_number IN (SELECT business_contact_number FROM holdout_customers)
    )
    SELECT {sql_literal(run_id)} AS run_id, business_contact_number, proposition_id, content_id, model_score, weighted_score,
           overall_rank AS rank, 'exploit' AS allocation, current_timestamp() AS run_timestamp, current_date() AS snapshotdate,
           current_timestamp() AS audit_timestamp, {sql_literal(settings.source_system)} AS source_system
    FROM exploit_selected
    UNION ALL
    SELECT {sql_literal(run_id)}, business_contact_number, proposition_id, content_id, model_score, weighted_score,
           overall_rank, 'explore', current_timestamp(), current_date(), current_timestamp(), {sql_literal(settings.source_system)}
    FROM explore_selected
    UNION ALL
    SELECT {sql_literal(run_id)}, business_contact_number, proposition_id, content_id, model_score, weighted_score,
           overall_rank, 'holdout', current_timestamp(), current_date(), current_timestamp(), {sql_literal(settings.source_system)}
    FROM holdout_selected
    """

    cdo_insert = f"""
    INSERT INTO {settings.fq_schema}.contact_decision_output
    SELECT business_contact_number, content_id, run_timestamp, snapshotdate, current_timestamp() AS audit_timestamp, {sql_literal(settings.source_system)} AS source_system
    FROM {settings.fq_schema}.next_best_experience
    WHERE run_id = {sql_literal(run_id)} AND allocation IN ('exploit', 'explore')
    """

    sql = f"""
    BEGIN ATOMIC
      -- keep prior snapshots: the recent-contact (follow-up) exclusion rules read contact history
      DELETE FROM {settings.fq_schema}.contact_decision_output WHERE snapshotdate = current_date();
      DELETE FROM {settings.fq_schema}.next_best_experience;
      {nbe_insert};
      {cdo_insert};
    END;
    """
    db.execute(sql, wait_s=90)
    return simulation_summary(db)


def simulation_summary(db: StatementExecutionClient) -> Dict[str, Any]:
    latest = db.execute_one(
        f"SELECT max(run_timestamp) AS run_timestamp, max(snapshotdate) AS snapshotdate FROM {settings.fq_schema}.contact_decision_output"
    ) or {}
    by_prop = db.execute(
        f"""
        SELECT n.proposition_id, count(*) AS contacts
        FROM {settings.fq_schema}.next_best_experience n
        WHERE n.run_timestamp = (SELECT max(run_timestamp) FROM {settings.fq_schema}.next_best_experience)
          AND n.allocation IN ('exploit', 'explore')
        GROUP BY n.proposition_id
        ORDER BY contacts DESC, proposition_id
        """
    )
    by_channel = db.execute(
        f"""
        SELECT c.channel_code, count(*) AS contacts
        FROM {settings.fq_schema}.contact_decision_output d
        JOIN {settings.fq_schema}.content c ON c.content_id = d.content_id
        WHERE d.run_timestamp = (SELECT max(run_timestamp) FROM {settings.fq_schema}.contact_decision_output)
        GROUP BY c.channel_code
        ORDER BY contacts DESC, channel_code
        """
    )
    return {"latest": latest, "by_proposition": by_prop, "by_channel": by_channel}
