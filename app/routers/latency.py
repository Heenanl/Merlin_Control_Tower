from __future__ import annotations

import statistics
import time
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import CurrentUser
from app.db import LakebaseStore, StatementExecutionClient, lakebase_query
from app.registry import RULE_MATRIX_SQL, overview_sql
from app.services.dependencies import current_user_dep
from app.settings import settings

router = APIRouter(prefix="/api/latency", tags=["latency"])

FQ = settings.fq_schema
# The app's own read queries; identical SQL text on both backends (except the overview, see registry).
BENCHMARK_QUERIES = [
    ("Single record lookup", lambda b: f"SELECT * FROM {FQ}.content WHERE content_id = '1284BM'"),
    ("Propositions list", lambda b: f"SELECT * FROM {FQ}.proposition ORDER BY proposition_id"),
    ("Content catalogue", lambda b: f"SELECT * FROM {FQ}.content ORDER BY content_id"),
    ("Overview KPIs", overview_sql),
    ("Change request list", lambda b: f"SELECT request_id, title, status, updated_at FROM {FQ}.change_request ORDER BY coalesce(updated_at, created_at) DESC LIMIT 200"),
    ("Rule x Content matrix", lambda b: RULE_MATRIX_SQL),
]


def _timed(fn) -> tuple[float, int]:
    started = time.perf_counter()
    rows = fn()
    return (time.perf_counter() - started) * 1000, len(rows)


@router.get("/benchmark")
def benchmark(runs: int = Query(3, ge=1, le=5), user: CurrentUser = Depends(current_user_dep)) -> Dict[str, Any]:
    """Run each query `runs` times on the SQL warehouse (UC) and on Lakebase, alternating backends.
    Times are server-side, from the app to the database and back, so browser and network time is excluded."""
    if not LakebaseStore.configured():
        raise HTTPException(status_code=400, detail="Lakebase is not configured for this app")
    uc = StatementExecutionClient(user.access_token)
    results: List[Dict[str, Any]] = []
    for label, sql_for in BENCHMARK_QUERIES:
        uc_ms: List[float] = []
        lb_ms: List[float] = []
        uc_rows = lb_rows = 0
        for _ in range(runs):
            ms, uc_rows = _timed(lambda: uc.execute(sql_for("uc"), row_limit=5000))
            uc_ms.append(ms)
            ms, lb_rows = _timed(lambda: lakebase_query(sql_for("lakebase"), row_limit=5000))
            lb_ms.append(ms)
        results.append(
            {
                "query": label,
                "rows_uc": uc_rows,
                "rows_lakebase": lb_rows,
                "same_row_count": uc_rows == lb_rows,
                "uc_median_ms": round(statistics.median(uc_ms), 1),
                "uc_min_ms": round(min(uc_ms), 1),
                "lakebase_median_ms": round(statistics.median(lb_ms), 1),
                "lakebase_min_ms": round(min(lb_ms), 1),
                "speedup": round(statistics.median(uc_ms) / max(statistics.median(lb_ms), 0.1), 1),
            }
        )
    return {"runs": runs, "warehouse_id": settings.warehouse_id, "lakebase_endpoint": settings.lakebase_endpoint, "results": results}
