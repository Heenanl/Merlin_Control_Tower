from __future__ import annotations

import json
import time
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from uuid import uuid4

import requests
from fastapi import HTTPException

from app.settings import settings


class StatementExecutionClient:
    """Minimal SQL Statement Execution client for Databricks Apps.

    The app runs each statement with the signed-in user's forwarded access token so Unity Catalog
    permissions are applied per user. A SQL warehouse resource must be attached to the app and
    exposed as DATABRICKS_WAREHOUSE_ID in app.yaml.
    """

    def __init__(self, access_token: str):
        if not access_token:
            raise HTTPException(status_code=401, detail="Missing x-forwarded-access-token header")
        if not settings.databricks_host:
            raise HTTPException(status_code=500, detail="Missing Databricks host configuration")
        if not settings.warehouse_id:
            raise HTTPException(status_code=500, detail="Missing DATABRICKS_WAREHOUSE_ID app resource")
        self.base_url = settings.databricks_host.rstrip("/")
        self.headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    def execute(self, statement: str, *, wait_s: int = 30, row_limit: int = 5000) -> List[Dict[str, Any]]:
        payload = {
            "statement": statement,
            "warehouse_id": settings.warehouse_id,
            "wait_timeout": f"{wait_s}s",
            "format": "JSON_ARRAY",
            "disposition": "INLINE",
            "row_limit": row_limit,
            "byte_limit": 10_000_000,
        }
        response = requests.post(f"{self.base_url}/api/2.0/sql/statements/", headers=self.headers, json=payload, timeout=60)
        self._raise_for_status(response)
        body = response.json()
        statement_id = body["statement_id"]
        status = body.get("status", {}).get("state")
        while status in {"PENDING", "RUNNING"}:
            time.sleep(0.4)
            poll = requests.get(f"{self.base_url}/api/2.0/sql/statements/{statement_id}", headers=self.headers, timeout=60)
            self._raise_for_status(poll)
            body = poll.json()
            status = body.get("status", {}).get("state")
        if status != "SUCCEEDED":
            err = body.get("status", {}).get("error", {})
            raise HTTPException(status_code=400, detail=err.get("message") or f"SQL statement failed: {status}")
        return self._rows_from_body(body)

    def execute_one(self, statement: str) -> Optional[Dict[str, Any]]:
        rows = self.execute(statement, row_limit=1)
        return rows[0] if rows else None

    def execute_scalar(self, statement: str, *, default: Any = None) -> Any:
        row = self.execute_one(statement)
        if not row:
            return default
        return next(iter(row.values()), default)

    @staticmethod
    def _coerce(value: Any, type_name: str) -> Any:
        """JSON_ARRAY results return every value as a string; restore Python types."""
        if value is None:
            return None
        t = (type_name or "").upper()
        if t == "BOOLEAN":
            return str(value).lower() == "true"
        if t in {"INT", "LONG", "SHORT", "BYTE", "INTEGER", "BIGINT", "SMALLINT", "TINYINT"}:
            return int(value)
        if t in {"DOUBLE", "FLOAT", "DECIMAL"}:
            return float(value)
        if t == "ARRAY":
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    @classmethod
    def _rows_from_body(cls, body: Mapping[str, Any]) -> List[Dict[str, Any]]:
        manifest = body.get("manifest", {})
        cols = manifest.get("schema", {}).get("columns", [])
        names = [c["name"] for c in cols]
        types = [c.get("type_name", "STRING") for c in cols]
        data = body.get("result", {}).get("data_array") or []
        return [
            {n: cls._coerce(v, t) for n, v, t in zip(names, row, types)}
            for row in data
        ]

    def column_types(self, table: str) -> Dict[str, str]:
        rows = self.execute(
            f"""
            SELECT column_name, full_data_type FROM {settings.catalog}.information_schema.columns
            WHERE table_schema = {sql_literal(settings.schema)} AND table_name = {sql_literal(table)}
            """
        )
        return {r["column_name"]: r["full_data_type"] for r in rows}

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        if response.ok:
            return
        try:
            message = response.json().get("message") or response.text
        except Exception:
            message = response.text
        raise HTTPException(status_code=response.status_code, detail=message)


def sql_ident(value: str) -> str:
    if not value or not value.replace("_", "").replace(".", "").isalnum():
        raise HTTPException(status_code=400, detail=f"Unsafe identifier: {value}")
    return value


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return f"TIMESTAMP '{value.isoformat(sep=' ', timespec='seconds')}'"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, (dict, list)):
        value = json.dumps(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def typed_literal(value: Any, data_type: Optional[str]) -> str:
    """Literal with an explicit CAST to the target column type, avoiding store-assignment surprises."""
    if value is None:
        return f"CAST(NULL AS {data_type})" if data_type else "NULL"
    if data_type and data_type.lower().startswith("array") and isinstance(value, list):
        return f"CAST(from_json({sql_literal(json.dumps(value))}, {sql_literal(data_type)}) AS {data_type})"
    lit = sql_literal(value)
    return f"CAST({lit} AS {data_type})" if data_type else lit


def row_to_json(row: Mapping[str, Any]) -> str:
    return sql_literal(json.dumps(row, default=str))


def qualify(table: str) -> str:
    return f"{settings.fq_schema}.{sql_ident(table)}"


def where_eq(keys: Mapping[str, Any]) -> str:
    if not keys:
        raise HTTPException(status_code=400, detail="Missing record key")
    return " AND ".join(f"{sql_ident(col)} = {sql_literal(value)}" for col, value in keys.items())


def comma_list(items: Iterable[str]) -> str:
    return ", ".join(items)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"
