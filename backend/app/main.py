from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .bilibili import BiliClient, BiliError
from .config import DEFAULT_SETTINGS, ensure_dirs
from .database import connect, init_db, read_settings, row_to_dict, write_settings
from .security import create_session, hash_password, current_user, verify_password
from .tasks import cancel_task, create_tasks, get_task, list_tasks, manager, pause_task, resume_task


app = FastAPI(title="Bili23 Web", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginPayload(BaseModel):
    username: str
    password: str


class BootstrapPayload(BaseModel):
    username: str = "admin"
    password: str


class ParsePayload(BaseModel):
    url: str


class CreateTasksPayload(BaseModel):
    episodes: list[dict[str, Any]]
    options: dict[str, Any] = {}


class BatchJobPayload(BaseModel):
    url: str
    options: dict[str, Any] = {}


class SettingsPayload(BaseModel):
    values: dict[str, Any]


@app.on_event("startup")
def startup() -> None:
    ensure_dirs()
    init_db()
    manager.start()


@app.get("/api/health")
def health() -> dict[str, Any]:
    with connect() as db:
        users = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    return {"ok": True, "setup_required": users == 0, "time": int(time.time())}


@app.post("/api/auth/bootstrap")
def bootstrap(payload: BootstrapPayload) -> dict[str, Any]:
    with connect() as db:
        users = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if users:
            raise HTTPException(status_code=409, detail="Admin user already exists")
        db.execute(
            "INSERT INTO users(username, password_hash, created_at) VALUES(?, ?, ?)",
            (payload.username, hash_password(payload.password), int(time.time())),
        )
        user = db.execute("SELECT id, username FROM users WHERE username = ?", (payload.username,)).fetchone()
    token = create_session(user["id"])
    return {"token": token, "user": row_to_dict(user)}


@app.post("/api/auth/login")
def login(payload: LoginPayload) -> dict[str, Any]:
    with connect() as db:
        user = db.execute("SELECT * FROM users WHERE username = ?", (payload.username,)).fetchone()
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"token": create_session(user["id"]), "user": {"id": user["id"], "username": user["username"]}}


@app.get("/api/me")
def me(user: dict = Depends(current_user)) -> dict:
    return user


@app.get("/api/settings")
def settings(user: dict = Depends(current_user)) -> dict[str, Any]:
    return read_settings()


@app.put("/api/settings")
def update_settings(payload: SettingsPayload, user: dict = Depends(current_user)) -> dict[str, Any]:
    allowed = set(DEFAULT_SETTINGS)
    return write_settings({k: v for k, v in payload.values.items() if k in allowed})


@app.get("/api/bilibili/account")
async def bili_account(user: dict = Depends(current_user)) -> dict[str, Any]:
    try:
        return await BiliClient().account()
    except Exception as exc:
        return {"is_login": False, "error": str(exc)}


@app.post("/api/bilibili/qrcode/start")
async def qrcode_start(user: dict = Depends(current_user)) -> dict[str, str]:
    try:
        return await BiliClient().qrcode_start()
    except BiliError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/bilibili/qrcode/status")
async def qrcode_status(user: dict = Depends(current_user)) -> dict[str, Any]:
    try:
        return await BiliClient().qrcode_status()
    except BiliError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/parse")
async def parse(payload: ParsePayload, user: dict = Depends(current_user)) -> dict[str, Any]:
    try:
        return await BiliClient().parse_url(payload.url)
    except BiliError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/tasks")
def create_download_tasks(payload: CreateTasksPayload, user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    if not payload.episodes:
        raise HTTPException(status_code=400, detail="请选择至少一个条目")
    return create_tasks(payload.episodes, payload.options)


@app.post("/api/batch-jobs")
async def create_batch_job(payload: BatchJobPayload, user: dict = Depends(current_user)) -> dict[str, Any]:
    parsed = await BiliClient().parse_url(payload.url)
    tasks = create_tasks(parsed["episodes"], {"source": payload.url, **payload.options})
    return {"source": parsed, "tasks": tasks, "total": len(tasks)}


@app.get("/api/tasks")
def tasks(status: str | None = None, user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    return list_tasks(status)


@app.get("/api/tasks/{task_id}")
def task_detail(task_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/api/tasks/{task_id}/pause")
def task_pause(task_id: str, user: dict = Depends(current_user)) -> dict[str, bool]:
    pause_task(task_id)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/resume")
def task_resume(task_id: str, user: dict = Depends(current_user)) -> dict[str, bool]:
    resume_task(task_id)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/cancel")
def task_cancel(task_id: str, user: dict = Depends(current_user)) -> dict[str, bool]:
    cancel_task(task_id)
    return {"ok": True}


@app.get("/api/logs")
def logs(task_id: str | None = None, user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    sql = "SELECT * FROM logs"
    args: tuple[Any, ...] = ()
    if task_id:
        sql += " WHERE task_id = ?"
        args = (task_id,)
    sql += " ORDER BY id DESC LIMIT 300"
    with connect() as db:
        return [dict(row) for row in db.execute(sql, args).fetchall()]


@app.get("/api/files")
def files(user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    root = Path(read_settings()["download_dir"]).expanduser()
    if not root.exists():
        return []
    result = []
    for path in root.rglob("*"):
        if path.is_file():
            result.append({"name": path.name, "path": str(path), "size": path.stat().st_size, "mtime": int(path.stat().st_mtime)})
    return sorted(result, key=lambda item: item["mtime"], reverse=True)[:500]


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
