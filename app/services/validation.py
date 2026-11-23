from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping

from fastapi import HTTPException

from app.registry import TABLES, get_table
from app.settings import settings

CONTENT_ID_RE = re.compile(r"^[0-9]{4}[A-Z]{2}$")


def require_supported_table(table_name: str) -> None:
    if table_name not in TABLES:
        raise HTTPException(status_code=400, detail=f"Unsupported table: {table_name}")


def validate_keys(table_name: str, key: Mapping[str, Any]) -> None:
    spec = get_table(table_name)
    missing = [k for k in spec.primary_keys if k not in key]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing key columns for {table_name}: {', '.join(missing)}")


def validate_row(table_name: str, row: Mapping[str, Any], *, for_create: bool) -> None:
    spec = get_table(table_name)
    allowed = set(spec.create_columns if for_create else spec.editable_columns + spec.primary_keys)
    extra = sorted(set(row) - allowed)
    if extra:
        raise HTTPException(status_code=400, detail=f"Unsupported columns for {table_name}: {', '.join(extra)}")

    if table_name == "content":
        if "content_id" in row and not CONTENT_ID_RE.match(str(row["content_id"])):
            raise HTTPException(status_code=400, detail="content_id must match 4 digits + 2 capital letters, e.g. 1217EE")
        for key in ["message_type", "channel_code", "language_code", "content_frequency_level"]:
            if key in row and row[key] not in settings.enum_values[key]:
                raise HTTPException(status_code=400, detail=f"Invalid {key}: {row[key]}")
    if table_name == "eligibility_rule":
        for key in ["eligibility_rule_level", "eligibility_rule_type", "status"]:
            if key in row and row[key] not in settings.enum_values[key]:
                raise HTTPException(status_code=400, detail=f"Invalid {key}: {row[key]}")
    if table_name == "model_metadata" and "value_type" in row and row["value_type"] not in {"int", "double", "string"}:
        raise HTTPException(status_code=400, detail="model_metadata.value_type must be int, double or string")


def validate_bulk_rows(table_name: str, rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    validated = []
    for row in rows:
        validate_row(table_name, row, for_create=True)
        validated.append(dict(row))
    return validated


def validate_share_sum(operations: Iterable[Mapping[str, Any]]) -> None:
    pending = {}
    for op in operations:
        if op.get("table") != "model_metadata":
            continue
        after = op.get("after") or {}
        if after.get("key") in {"exploit_share", "explore_share", "holdout_share"} and "value" in after:
            pending[after["key"]] = float(after["value"])
    if {"exploit_share", "explore_share"}.issubset(pending) and abs(pending["exploit_share"] + pending["explore_share"] - 1.0) > 1e-9:
        raise HTTPException(status_code=400, detail="exploit_share + explore_share must sum to 1")
    if "holdout_share" in pending and not 0 <= pending["holdout_share"] <= 1:
        raise HTTPException(status_code=400, detail="holdout_share must be between 0 and 1")
