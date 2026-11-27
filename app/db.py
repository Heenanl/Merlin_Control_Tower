from __future__ import annotations

import json
import re
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union
from uuid import uuid4

import requests
from fastapi import HTTPException

from app.settings import settings


# --- per-request latency tracking --------------------------------------------------------------
# The middleware in main.py starts a list per request; every statement appends (backend, kind, ms).
_timings: ContextVar[Optional[list]] = ContextVar("merlin_db_timings", default=None)


def start_timing() -> list:
    timings: list = []
    _timings.set(timings)
    return timings


def record_timing(backend: str, kind: str, ms: float) -> None:
    timings = _timings.get()
    if timings is not None:
        timings.append((backend, kind, round(ms, 1)))


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
        host = settings.databricks_host.rstrip("/")
        # Databricks Apps injects DATABRICKS_HOST without a scheme
        self.base_url = host if host.startswith("http") else f"https://{host}"
        self.headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    backend = "uc"

    def execute(self, statement: str, *, wait_s: int = 30, row_limit: int = 5000) -> List[Dict[str, Any]]:
        started = time.perf_counter()
        try:
            return self._execute(statement, wait_s=wait_s, row_limit=row_limit)
        finally:
            record_timing("uc", "write" if _WRITE_LABEL_RE.match(statement) else "read", (time.perf_counter() - started) * 1000)

    def _execute(self, statement: str, *, wait_s: int = 30, row_limit: int = 5000) -> List[Dict[str, Any]]:
        payload = {
            "statement": statement,
            "warehouse_id": settings.warehouse_id,
            # API accepts 0 or 5-50s; longer statements keep running and are polled below
            "wait_timeout": f"{max(5, min(int(wait_s), 50))}s",
            "on_wait_timeout": "CONTINUE",
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
        deadline = time.time() + max(wait_s, 50) + 240
        while status in {"PENDING", "RUNNING"}:
            if time.time() > deadline:
                raise HTTPException(status_code=504, detail="SQL statement is still running; try again shortly")
            time.sleep(1.0)
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

    def submit(self, statement: str) -> str:
        """Start a SQL statement and return its statement_id without waiting."""
        payload = {
            "statement": statement,
            "warehouse_id": settings.warehouse_id,
            "wait_timeout": "5s",
            "on_wait_timeout": "CONTINUE",
            "format": "JSON_ARRAY",
            "disposition": "INLINE",
        }
        response = requests.post(
            f"{self.base_url}/api/2.0/sql/statements/",
            headers=self.headers,
            json=payload,
            timeout=15,
        )
        self._raise_for_status(response)
        return response.json()["statement_id"]

    def get_statement_result(self, statement_id: str) -> Dict[str, Any]:
        """Poll a submitted statement. Returns {state, rows} where rows is None unless SUCCEEDED."""
        poll = requests.get(
            f"{self.base_url}/api/2.0/sql/statements/{statement_id}",
            headers=self.headers,
            timeout=15,
        )
        self._raise_for_status(poll)
        body = poll.json()
        state = body.get("status", {}).get("state", "UNKNOWN")
        if state == "SUCCEEDED":
            return {"state": state, "rows": self._rows_from_body(body)}
        if state in {"FAILED", "CANCELED", "CLOSED"}:
            err = body.get("status", {}).get("error", {})
            raise HTTPException(
                status_code=400,
                detail=err.get("message") or f"SQL statement {state}",
            )
        return {"state": state, "rows": None}  # PENDING or RUNNING

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
    # Databricks SQL joins adjacent literals ('a''b' -> 'ab'), so doubling quotes silently drops them.
    # Escape with backslashes instead (backslash first).
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
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


# --- Lakebase (Postgres) store -------------------------------------------------------------------
# Lakebase is the app's main store for config, change requests and the audit log. Unity Catalog keeps a
# mirror of the same tables because the simulation joins them with customers, scores and contact history.
# The app's SQL is written in Databricks syntax; the Lakebase schema adds `string`/`double` domains and a
# datediff() function, and each session accepts backslash escapes, so the same statement text runs on both.

_WRITE_RE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|MERGE)\b", re.IGNORECASE)
_WRITE_LABEL_RE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|MERGE|BEGIN)\b", re.IGNORECASE)  # timing label only
# Tables that only exist in Unity Catalog: statements touching them always run on the SQL warehouse.
_UC_ONLY_RE = re.compile(r"\.(customer|model_score|contact_decision_output|next_best_experience)\b", re.IGNORECASE)
_SESSION_SETTINGS = [
    "SET search_path = {schema}, public",
    "SET standard_conforming_strings = off",  # Databricks-style backslash escapes in literals
    "SET escape_string_warning = off",
    "SET TIME ZONE 'UTC'",
    "SET statement_timeout = '60s'",
]


def _to_postgres(statement: str) -> str:
    return statement.replace("current_timestamp()", "current_timestamp").replace("current_date()", "current_date")


def _json_value(value: Any) -> Any:
    """Match the warehouse client's JSON output so pages behave the same on both backends."""
    if isinstance(value, datetime):
        if value.tzinfo:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(timespec="milliseconds") + "Z"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


class LakebaseStore:
    """Process-wide psycopg3 pool. Connects as the app service principal (PGUSER) with a fresh Lakebase
    OAuth credential per new connection; connections are recycled before the 1-hour token expiry."""

    _pool = None
    _lock = threading.Lock()

    @staticmethod
    def configured() -> bool:
        return bool(settings.pg_host and settings.lakebase_endpoint)

    @classmethod
    def pool(cls):
        if cls._pool is not None:
            return cls._pool
        with cls._lock:
            if cls._pool is None:
                import psycopg
                from databricks.sdk import WorkspaceClient
                from psycopg_pool import ConnectionPool

                workspace = WorkspaceClient()
                endpoint = settings.lakebase_endpoint

                class TokenConnection(psycopg.Connection):
                    @classmethod
                    def connect(cls_, conninfo: str = "", **kwargs):
                        kwargs["password"] = workspace.postgres.generate_database_credential(endpoint=endpoint).token
                        return super().connect(conninfo, **kwargs)

                def configure(conn) -> None:
                    for setting in _SESSION_SETTINGS:
                        conn.execute(setting.format(schema=settings.pg_schema))
                    conn.commit()

                cls._pool = ConnectionPool(
                    conninfo="",
                    connection_class=TokenConnection,
                    kwargs={
                        "host": settings.pg_host,
                        "port": settings.pg_port,
                        "dbname": settings.pg_database,
                        "user": settings.pg_user,
                        "sslmode": "require",
                    },
                    configure=configure,
                    check=ConnectionPool.check_connection,  # survives scale-to-zero restarts
                    min_size=1,
                    max_size=8,
                    max_lifetime=2700,
                    open=True,
                )
        return cls._pool


def _pg_rows(cursor) -> List[Dict[str, Any]]:
    if cursor.description is None:
        return []
    names = [d.name for d in cursor.description]
    return [{n: _json_value(v) for n, v in zip(names, row)} for row in cursor.fetchall()]


def lakebase_query(statement: str, *, row_limit: int = 5000) -> List[Dict[str, Any]]:
    started = time.perf_counter()
    try:
        with LakebaseStore.pool().connection() as conn:
            cur = conn.execute(_to_postgres(statement))
            rows = _pg_rows(cur)
            return rows[:row_limit]
    except HTTPException:
        raise
    except Exception as exc:  # surface Postgres errors like warehouse errors
        raise HTTPException(status_code=400, detail=f"Lakebase: {exc}") from exc
    finally:
        record_timing("lakebase", "write" if _WRITE_RE.match(statement) else "read", (time.perf_counter() - started) * 1000)


@dataclass(frozen=True)
class Guard:
    """Abort a guarded write unless `count_sql` returns exactly `expected`."""

    count_sql: str
    expected: int
    message: str

    def databricks_sql(self) -> str:
        return (
            f"IF ({self.count_sql}) <> {self.expected} THEN "
            f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = {sql_literal(self.message)}; END IF;"
        )


Step = Union[Guard, str]


class ConfigClient:
    """DB client used by every request.

    * Reads go to the backend chosen with the UI toggle (header X-Merlin-Backend): `uc` (SQL warehouse) or
      `lakebase`. Statements on UC-only tables (customers, scores, contact history) always use the warehouse.
    * Writes always land in both stores in one unit: open a Lakebase transaction, apply the change there,
      apply it to UC (one BEGIN ATOMIC), then commit Lakebase. If UC fails, Lakebase rolls back.
      Both stores get the same timestamp literal, so they stay identical row for row.
    """

    def __init__(self, access_token: str, backend: str = "uc"):
        self.uc = StatementExecutionClient(access_token)
        self.lakebase_available = LakebaseStore.configured()
        self.backend = "lakebase" if backend == "lakebase" and self.lakebase_available else "uc"

    # --- reads ------------------------------------------------------------------------------------
    def execute(self, statement: str, *, wait_s: int = 30, row_limit: int = 5000) -> List[Dict[str, Any]]:
        if _WRITE_RE.match(statement):
            self.run_guarded([statement], wait_s=wait_s)
            return []
        if self.backend == "lakebase" and not _UC_ONLY_RE.search(statement):
            return lakebase_query(statement, row_limit=row_limit)
        return self.uc.execute(statement, wait_s=wait_s, row_limit=row_limit)

    def execute_one(self, statement: str) -> Optional[Dict[str, Any]]:
        rows = self.execute(statement, row_limit=1)
        return rows[0] if rows else None

    def execute_scalar(self, statement: str, *, default: Any = None) -> Any:
        row = self.execute_one(statement)
        if not row:
            return default
        return next(iter(row.values()), default)

    # The simulation runs on the warehouse (it needs customers and scores).
    def submit(self, statement: str) -> str:
        return self.uc.submit(statement)

    def get_statement_result(self, statement_id: str) -> Dict[str, Any]:
        return self.uc.get_statement_result(statement_id)

    _column_type_cache: Dict[str, Dict[str, str]] = {}

    def column_types(self, table: str) -> Dict[str, str]:
        # Same schema in both stores; UC types also work in Lakebase (string/double are domains there).
        if table not in ConfigClient._column_type_cache:
            ConfigClient._column_type_cache[table] = self.uc.column_types(table)
        return ConfigClient._column_type_cache[table]

    # --- writes -----------------------------------------------------------------------------------
    def run_guarded(self, steps: Sequence[Step], *, wait_s: int = 60) -> None:
        """Apply guards + DML atomically to Lakebase and UC."""
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="milliseconds")
        pin = lambda sql: sql.replace("current_timestamp()", f"TIMESTAMP '{now}'").replace("current_date()", f"DATE '{now[:10]}'")
        steps = [Guard(pin(s.count_sql), s.expected, s.message) if isinstance(s, Guard) else pin(s) for s in steps]

        def uc_write() -> None:
            if len(steps) == 1 and isinstance(steps[0], str):
                self.uc.execute(steps[0], wait_s=wait_s)
                return
            body = "\n  ".join(s.databricks_sql() if isinstance(s, Guard) else s.rstrip().rstrip(";") + ";" for s in steps)
            self.uc.execute(f"BEGIN ATOMIC\n  {body}\nEND;", wait_s=wait_s)

        if not self.lakebase_available:
            uc_write()
            return
        started = time.perf_counter()
        try:
            with LakebaseStore.pool().connection() as conn:
                with conn.transaction():
                    # serialise config writers so guards and writes see a stable state
                    conn.execute("SELECT pg_advisory_xact_lock(hashtext('merlin_config_write'))")
                    for step in steps:
                        if isinstance(step, Guard):
                            count = conn.execute(_to_postgres(step.count_sql)).fetchone()[0]
                            if int(count) != step.expected:
                                raise HTTPException(status_code=409, detail=step.message)
                        else:
                            conn.execute(_to_postgres(step))
                    lakebase_ms = (time.perf_counter() - started) * 1000
                    record_timing("lakebase", "write", lakebase_ms)
                    uc_write()  # raises -> the Lakebase transaction rolls back
                    commit_started = time.perf_counter()
                record_timing("lakebase", "commit", (time.perf_counter() - commit_started) * 1000)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Lakebase: {exc}") from exc

    def record_simulation_run(self, latest: Mapping[str, Any]) -> None:
        """Copy the last run timestamp to Lakebase so the overview can show it without touching UC."""
        if not self.lakebase_available or not latest.get("run_timestamp"):
            return
        sql = (
            f"INSERT INTO {settings.pg_schema}.simulation_status VALUES (1, {sql_literal(latest['run_timestamp'])}, "
            f"{sql_literal(latest.get('snapshotdate'))}, current_timestamp, {sql_literal(settings.source_system)}) "
            "ON CONFLICT (id) DO UPDATE SET last_simulation_run = EXCLUDED.last_simulation_run, "
            "last_snapshotdate = EXCLUDED.last_snapshotdate, audit_timestamp = EXCLUDED.audit_timestamp"
        )
        with LakebaseStore.pool().connection() as conn:
            conn.execute(sql)
