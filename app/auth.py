from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Set

from fastapi import HTTPException, Request

from app.db import StatementExecutionClient, sql_literal
from app.settings import settings


@dataclass
class CurrentUser:
    user_id: str
    email: str
    preferred_username: str
    access_token: str
    roles: Set[str]

    def require_any(self, allowed: Iterable[str]) -> None:
        if self.roles.intersection(set(allowed)):
            return
        raise HTTPException(status_code=403, detail=f"Requires one of roles: {', '.join(sorted(allowed))}")


async def forwarded_identity(request: Request) -> CurrentUser:
    email = (request.headers.get("x-forwarded-email") or "").strip().lower()
    user_id = (request.headers.get("x-forwarded-user") or email).strip().lower()
    preferred_username = (request.headers.get("x-forwarded-preferred-username") or email or user_id).strip()
    access_token = request.headers.get("x-forwarded-access-token") or ""
    if not email:
        raise HTTPException(status_code=401, detail="Missing x-forwarded-email header. Run inside Databricks Apps.")

    db = StatementExecutionClient(access_token)
    rows = db.execute(
        f"SELECT role FROM {settings.fq_schema}.app_roles WHERE lower(user_email) = {sql_literal(email)} ORDER BY role",
        row_limit=10,
    )
    roles = {r["role"] for r in rows} or {"viewer"}
    return CurrentUser(user_id=user_id, email=email, preferred_username=preferred_username, access_token=access_token, roles=roles)
