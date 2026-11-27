from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Set


@dataclass(frozen=True)
class AppSettings:
    app_name: str = "Merlin Control Tower"
    catalog: str = os.getenv("MERLIN_CATALOG", "merlin_demo")
    schema: str = os.getenv("MERLIN_SCHEMA", "merlin_demo")
    warehouse_id: str = os.getenv("DATABRICKS_WAREHOUSE_ID", "")
    databricks_host: str = (
        os.getenv("DATABRICKS_HOST")
        or os.getenv("DATABRICKS_SERVER_HOSTNAME")
        or os.getenv("DB_HOST")
        or ""
    )
    source_system: str = "merlin_control_tower"
    # Lakebase (Postgres): PG* variables are injected by the app's `postgres` resource;
    # LAKEBASE_ENDPOINT comes from app.yaml (valueFrom: postgres). Database and schema mirror catalog/schema.
    pg_host: str = os.getenv("PGHOST", "")
    pg_port: int = int(os.getenv("PGPORT", "5432"))
    pg_database: str = os.getenv("MERLIN_PG_DATABASE", os.getenv("MERLIN_CATALOG", "merlin_demo"))
    pg_schema: str = os.getenv("MERLIN_SCHEMA", "merlin_demo")
    pg_user: str = os.getenv("PGUSER", "")
    lakebase_endpoint: str = os.getenv("LAKEBASE_ENDPOINT", "")
    # Backend used when the browser has not picked one with the toggle: uc or lakebase
    default_backend: str = os.getenv("MERLIN_DEFAULT_BACKEND", "lakebase")
    # DEMO ONLY: lets one person draft and approve the same request. Off unless explicitly enabled.
    allow_self_approval: bool = os.getenv("MERLIN_ALLOW_SELF_APPROVAL", "false").strip().lower() == "true"
    stale_content_days: int = 300
    default_followup_days: int = 14
    reference_population: int = 5_000_000
    allowed_tables: Set[str] = field(
        default_factory=lambda: {
            "proposition",
            "content",
            "eligibility_rule",
            "eligibility_rule_set",
            "content_eligibility_rule",
            "model_metadata",
        }
    )
    mutable_tables: Set[str] = field(
        default_factory=lambda: {
            "proposition",
            "content",
            "eligibility_rule",
            "eligibility_rule_set",
            "content_eligibility_rule",
            "model_metadata",
            "change_request",
            "change_audit_log",
            "next_best_experience",
            "contact_decision_output",
        }
    )
    primary_keys: Dict[str, List[str]] = field(
        default_factory=lambda: {
            "proposition": ["proposition_id"],
            "content": ["content_id"],
            "eligibility_rule": ["eligibility_rule_id"],
            "eligibility_rule_set": ["eligibility_rule_set_id", "eligibility_rule_id"],
            "content_eligibility_rule": ["content_id", "eligibility_rule_id"],
            "model_metadata": ["key"],
            "change_request": ["request_id"],
            "change_audit_log": ["audit_id"],
        }
    )
    soft_delete_columns: Dict[str, str] = field(
        default_factory=lambda: {
            "proposition": "active_ind",
            "content": "active_content_ind",
            "eligibility_rule": "status",
            "eligibility_rule_set": "active_ind",
            "content_eligibility_rule": "active_ind",
        }
    )
    enum_values: Dict[str, List[str]] = field(
        default_factory=lambda: {
            "message_type": ["tip", "offer"],
            "channel_code": ["email", "push", "in_app", "sms"],
            "language_code": ["NL", "EN"],
            "eligibility_rule_level": [
                "customer_segment",
                "channel_code",
                "proposition",
                "optin",
                "content",
                "message_type",
                "language",
            ],
            "eligibility_rule_type": ["Include", "Exclude"],
            "status": ["live", "draft", "retired"],
            "role": ["drafter", "approver", "viewer"],
            "change_request_status": ["draft", "submitted", "approved", "rejected", "promoted", "reverted"],
            "content_frequency_level": ["weekly", "biweekly", "monthly", "quarterly"],
        }
    )

    @property
    def fq_schema(self) -> str:
        return f"{self.catalog}.{self.schema}"


settings = AppSettings()
