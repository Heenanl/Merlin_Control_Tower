from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.db import start_timing
from app.routers import change_requests, config, frontend, latency, overview, simulation
from app.settings import settings

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(frontend.router)
app.include_router(overview.router)
app.include_router(config.router)
app.include_router(change_requests.router)
app.include_router(simulation.router)
app.include_router(latency.router)


@app.middleware("http")
async def db_latency_headers(request: Request, call_next):
    """Expose per-request DB time so the UI can show the UC vs Lakebase difference.
    X-Merlin-Db: comma-separated backend:kind:ms entries, one per statement."""
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    timings = start_timing()
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Merlin-Server-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    response.headers["X-Merlin-Db"] = ",".join(f"{b}:{k}:{ms}" for b, k, ms in timings)
    return response

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
