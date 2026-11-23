from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter(tags=["frontend"])
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/config.json")
async def config() -> JSONResponse:
    return JSONResponse({"app": "Merlin Control Tower"})
