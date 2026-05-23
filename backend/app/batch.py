from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .bilibili import BiliClient
from .database import connect, log
from .tasks import create_tasks, get_task


TERMINAL_TASK_STATUS = {"completed", "failed", "cancelled"}
ACTIVE_JOB_STATUS = {"queued", "running"}


def _inflate(job: dict[str, Any]) -> dict[str, Any]:
    job["options"] = json.loads(job.get("options") or "{}")
    return job


def list_batch_jobs() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM batch_jobs ORDER BY created_at DESC").fetchall()
    return [_inflate(dict(row)) for row in rows]


def get_batch_job(job_id: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM batch_jobs WHERE id = ?", (job_id,)).fetchone()
    return _inflate(dict(row)) if row else None


def create_batch_job(source_url: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    now = int(time.time())
    options = options or {}
    job_id = str(uuid.uuid4())
    page_size = max(1, min(50, int(options.get("page_size") or _page_size_from_url(source_url) or 30)))
    with connect() as db:
        db.execute(
            """
            INSERT INTO batch_jobs(
                id, source_url, status, current_page, page_size, options, created_at, updated_at
            ) VALUES(?, ?, 'queued', 1, ?, ?, ?, ?)
            """,
            (job_id, source_url, page_size, json.dumps(options, ensure_ascii=False), now, now),
        )
    log("info", f"慢速批量下载任务已创建：{source_url}")
    manager.start()
    return get_batch_job(job_id) or {}


def update_batch_job(job_id: str, **values: Any) -> None:
    if not values:
        return
    values["updated_at"] = int(time.time())
    columns = ", ".join(f"{key} = ?" for key in values)
    with connect() as db:
        db.execute(f"UPDATE batch_jobs SET {columns} WHERE id = ?", (*values.values(), job_id))


def pause_batch_job(job_id: str) -> None:
    update_batch_job(job_id, status="paused")


def resume_batch_job(job_id: str) -> None:
    update_batch_job(job_id, status="queued", error="")
    manager.start()


def cancel_batch_job(job_id: str) -> None:
    update_batch_job(job_id, status="cancelled")


class BatchJobManager:
    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()

    def start(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            jobs = [job for job in list_batch_jobs() if job["status"] in ACTIVE_JOB_STATUS]
            for job in jobs:
                update_batch_job(job["id"], status="running")
                try:
                    asyncio.run(self._run_job(job["id"]))
                except Exception as exc:
                    update_batch_job(job["id"], status="failed", error=str(exc))
                    log("error", f"慢速批量下载失败：{exc}")
            time.sleep(2)

    async def _run_job(self, job_id: str) -> None:
        job = get_batch_job(job_id)
        if not job or job["status"] not in ACTIVE_JOB_STATUS:
            return

        client = BiliClient()
        page = max(1, int(job["current_page"]))
        page_size = max(1, min(50, int(job["page_size"])))
        while True:
            job = get_batch_job(job_id)
            if not job or job["status"] not in ACTIVE_JOB_STATUS:
                return

            page_url = _with_page(job["source_url"], page, page_size)
            parsed = await client.parse_url(page_url)
            pagination = parsed.get("pagination") or {}
            total_pages = max(1, int(pagination.get("total_pages") or 1))
            total_items = int(pagination.get("total_items") or len(parsed.get("episodes", [])))
            episodes = parsed.get("episodes", [])
            update_batch_job(
                job_id,
                current_page=page,
                page_size=page_size,
                total_pages=total_pages,
                total_items=total_items,
                total=max(total_items, int(job.get("total") or 0)),
            )

            if not episodes:
                update_batch_job(job_id, status="completed", completed_pages=page - 1)
                return

            options = dict(job.get("options") or {})
            options.update({"source": page_url, "batch_job_id": job_id, "batch_page": page, "slow_batch": True})
            tasks = create_tasks(episodes, options)
            task_ids = [task["id"] for task in tasks]
            update_batch_job(job_id, created=int(job.get("created") or 0) + len(task_ids))
            log("info", f"慢速批量下载第 {page}/{total_pages} 页已入队，共 {len(task_ids)} 个视频")

            await self._wait_page(job_id, task_ids)
            job = get_batch_job(job_id)
            if not job or job["status"] not in ACTIVE_JOB_STATUS:
                return

            update_batch_job(job_id, completed_pages=page, current_page=page + 1)
            if page >= total_pages:
                update_batch_job(job_id, status="completed")
                log("info", f"慢速批量下载完成：{job['source_url']}")
                return
            page += 1
            delay = max(0, int((job.get("options") or {}).get("page_delay_seconds") or 3))
            await asyncio.sleep(delay)

    async def _wait_page(self, job_id: str, task_ids: list[str]) -> None:
        while True:
            job = get_batch_job(job_id)
            if not job or job["status"] not in ACTIVE_JOB_STATUS:
                return
            statuses = [(get_task(task_id) or {}).get("status") for task_id in task_ids]
            if statuses and all(status in TERMINAL_TASK_STATUS for status in statuses):
                return
            await asyncio.sleep(3)


def _with_page(url: str, page: int, page_size: int) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["pn"] = [str(page)]
    query["ps"] = [str(page_size)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _page_size_from_url(url: str) -> int:
    query = parse_qs(urlparse(url).query)
    try:
        return int(query.get("ps", ["0"])[0])
    except (TypeError, ValueError):
        return 0


manager = BatchJobManager()
