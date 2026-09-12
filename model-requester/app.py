"""
model-requester — write-only task intake.

A tiny form that turns itself into a valid tasks.yaml entry via
scripts/add_task.py. This app does not monitor the board — the one GET
endpoint besides the form itself exists only to populate the "depends on"
multi-select with current task ids, not to report status.

Run:
    uvicorn app:app --port 8001 --app-dir model-requester
(or `cd model-requester && uvicorn app:app --port 8001`)

Run this from a machine with a normal git checkout of the repo (same
assumption as the scripts/*.py CLIs) — it pushes directly to that
checkout's remote.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from add_task import add_task  # noqa: E402
from common import CauceError, load_tasks, run_git  # noqa: E402

app = FastAPI(title="Cauce Model Requester")


class TaskRequest(BaseModel):
    title: str
    description: str
    scope: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    suggested_model: str = ""


@app.get("/api/tasks")
def list_tasks() -> JSONResponse:
    """Just enough state to populate the depends-on multi-select — a
    light git pull for freshness, then id/title/status only."""
    try:
        run_git(["pull", "--quiet"])
        data = load_tasks()
    except CauceError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    tasks = [
        {"id": t["id"], "title": t["title"], "status": t.get("status")}
        for t in data.get("tasks", [])
    ]
    return JSONResponse({"tasks": tasks})


@app.post("/api/tasks")
def create_task(req: TaskRequest) -> JSONResponse:
    try:
        new_id = add_task(
            title=req.title,
            description=req.description,
            scope=req.scope,
            depends_on=req.depends_on,
            suggested_model=req.suggested_model,
        )
    except CauceError as exc:
        # Validation or git-race failure — a clean 400, never a traceback.
        return JSONResponse(status_code=400, content={"error": str(exc)})
    return JSONResponse({"id": new_id})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(APP_DIR / "static" / "index.html")


app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
