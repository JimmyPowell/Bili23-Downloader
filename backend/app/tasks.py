from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .bilibili import BiliClient
from .database import connect, log, read_settings, row_to_dict, safe_child


RUNNING = {"queued", "parsing", "downloading", "merging", "additional_processing"}


def clean_name(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', "_", value).strip()
    return value[:160] or "bili23-video"


def list_tasks(status: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM tasks"
    args: tuple[Any, ...] = ()
    if status:
        sql += " WHERE status = ?"
        args = (status,)
    sql += " ORDER BY created_at DESC"
    with connect() as db:
        rows = db.execute(sql, args).fetchall()
    return [inflate_task(dict(row)) for row in rows]


def get_task(task_id: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return inflate_task(dict(row)) if row else None


def inflate_task(task: dict[str, Any]) -> dict[str, Any]:
    task["options"] = json.loads(task.get("options") or "{}")
    return task


def create_tasks(episodes: list[dict[str, Any]], options: dict[str, Any]) -> list[dict[str, Any]]:
    now = int(time.time())
    created = []
    settings = read_settings()
    download_root = settings["download_dir"]
    with connect() as db:
        for ep in episodes:
            task_id = str(uuid.uuid4())
            title = ep.get("title") or ep.get("bvid") or task_id
            out_dir = str(safe_child(download_root, clean_name(ep.get("bvid", "video"))))
            db.execute(
                """
                INSERT INTO tasks(
                    id, title, url, bvid, aid, cid, part, cover, status, output_dir, options,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (
                    task_id,
                    title,
                    ep.get("url", ""),
                    ep.get("bvid", ""),
                    int(ep.get("aid", 0)),
                    int(ep.get("cid", 0)),
                    int(ep.get("part", 1)),
                    ep.get("cover", ""),
                    out_dir,
                    json.dumps(options, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            created.append(task_id)
    for task_id in created:
        log("info", "任务已创建", task_id)
    return [get_task(tid) for tid in created if get_task(tid)]


def update_task(task_id: str, **values: Any) -> None:
    if not values:
        return
    values["updated_at"] = int(time.time())
    columns = ", ".join(f"{key} = ?" for key in values)
    with connect() as db:
        db.execute(f"UPDATE tasks SET {columns} WHERE id = ?", (*values.values(), task_id))


def pause_task(task_id: str) -> None:
    update_task(task_id, status="paused")
    log("info", "任务已暂停", task_id)


def resume_task(task_id: str) -> None:
    update_task(task_id, status="queued", error="")
    log("info", "任务已重新排队", task_id)
    manager.start()


def cancel_task(task_id: str) -> None:
    update_task(task_id, status="cancelled")
    log("info", "任务已取消", task_id)


class DownloadManager:
    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            settings = read_settings()
            max_concurrent = max(1, int(settings.get("max_concurrent", 1)))
            active = [t for t in list_tasks() if t["status"] in {"parsing", "downloading", "merging"}]
            if len(active) < max_concurrent:
                queued = [t for t in list_tasks("queued")]
                if queued:
                    task = queued[0]
                    threading.Thread(target=self._run_task, args=(task["id"],), daemon=True).start()
            time.sleep(1)

    def _run_task(self, task_id: str) -> None:
        try:
            asyncio.run(self._download(task_id))
        except Exception as exc:
            update_task(task_id, status="failed", error=str(exc), speed=0)
            log("error", str(exc), task_id)

    async def _download(self, task_id: str) -> None:
        task = get_task(task_id)
        if not task or task["status"] != "queued":
            return
        settings = read_settings()
        update_task(task_id, status="parsing", progress=0, error="")
        client = BiliClient()
        play = await client.playurl(task["bvid"], int(task["cid"]), int(settings.get("video_quality", 80)))
        task = get_task(task_id)
        if not task or task["status"] != "parsing":
            return
        streams = select_streams(play)
        out_dir = safe_child(settings["download_dir"], clean_name(task["bvid"]))
        out_dir.mkdir(parents=True, exist_ok=True)
        base_name = clean_name(
            str(settings.get("filename_template", "{title}-{part}"))
            .replace("{title}", task["title"])
            .replace("{part}", str(task["part"]))
            .replace("{bvid}", task["bvid"])
        )
        video_path = out_dir / f"{base_name}.video.m4s"
        audio_path = out_dir / f"{base_name}.audio.m4s"
        headers = client.headers(task["url"])
        async with httpx.AsyncClient(headers=headers, timeout=None, follow_redirects=True) as http:
            if streams.get("video") and not streams["video_size"]:
                streams["video_size"] = await estimate_size(http, streams["video"])
            if streams.get("audio") and not streams["audio_size"]:
                streams["audio_size"] = await estimate_size(http, streams["audio"])
            total = streams["video_size"] + streams["audio_size"]
            update_task(task_id, status="downloading", total_size=total, output_dir=str(out_dir))
            downloaded = 0
            if streams.get("video"):
                downloaded += await self._download_file(http, task_id, streams["video"], video_path, downloaded, total)
            if get_task(task_id)["status"] != "downloading":
                return
            if streams.get("audio"):
                downloaded += await self._download_file(http, task_id, streams["audio"], audio_path, downloaded, total)
        if get_task(task_id)["status"] != "downloading":
            return
        update_task(task_id, status="additional_processing")
        if settings.get("download_cover") and task.get("cover"):
            await download_binary(task["cover"], out_dir / f"{base_name}.jpg", headers)
        if settings.get("download_danmaku"):
            try:
                (out_dir / f"{base_name}.xml").write_bytes(await client.danmaku_xml(int(task["cid"])))
            except Exception as exc:
                log("warning", f"弹幕下载失败：{exc}", task_id)
        if settings.get("download_metadata"):
            (out_dir / f"{base_name}.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        final_path = video_path
        if settings.get("merge_av") and streams.get("audio") and shutil.which("ffmpeg"):
            update_task(task_id, status="merging")
            final_path = out_dir / f"{base_name}.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path), "-c", "copy", str(final_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if not settings.get("keep_parts"):
                video_path.unlink(missing_ok=True)
                audio_path.unlink(missing_ok=True)
        completed = int(time.time())
        update_task(
            task_id,
            status="completed",
            progress=100,
            speed=0,
            downloaded_size=total,
            output_file=str(final_path),
            completed_at=completed,
        )
        log("info", "任务下载完成", task_id)

    async def _download_file(
        self,
        http: httpx.AsyncClient,
        task_id: str,
        url: str,
        path: Path,
        offset_done: int,
        total: int,
    ) -> int:
        task = get_task(task_id)
        if not task:
            return 0
        existing = path.stat().st_size if path.exists() else 0
        mode = "ab" if existing else "wb"
        headers = {"Range": f"bytes={existing}-"} if existing else None
        size = existing
        last_time = time.monotonic()
        last_size = 0
        async with http.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            with path.open(mode) as fh:
                async for chunk in response.aiter_bytes(1024 * 256):
                    status = get_task(task_id)["status"]
                    if status in {"paused", "cancelled"}:
                        return size
                    if not chunk:
                        continue
                    fh.write(chunk)
                    size += len(chunk)
                    done = offset_done + size
                    now = time.monotonic()
                    if now - last_time >= 1:
                        speed = int((size - last_size) / max(now - last_time, 0.01))
                        progress = round((done / total) * 100, 2) if total else 0
                        update_task(task_id, downloaded_size=done, speed=speed, progress=progress)
                        last_time = now
                        last_size = size
        return size


def select_streams(play: dict[str, Any]) -> dict[str, Any]:
    dash = play.get("dash") or {}
    videos = dash.get("video") or []
    audios = dash.get("audio") or []
    video = sorted(videos, key=lambda item: item.get("bandwidth", 0), reverse=True)[0] if videos else None
    audio = sorted(audios, key=lambda item: item.get("bandwidth", 0), reverse=True)[0] if audios else None
    if not video and play.get("durl"):
        item = play["durl"][0]
        return {"video": item["url"], "audio": "", "video_size": int(item.get("size", 0)), "audio_size": 0}
    return {
        "video": (video or {}).get("baseUrl") or (video or {}).get("base_url", ""),
        "audio": (audio or {}).get("baseUrl") or (audio or {}).get("base_url", ""),
        "video_size": int((video or {}).get("size") or 0),
        "audio_size": int((audio or {}).get("size") or 0),
    }


async def download_binary(url: str, path: Path, headers: dict[str, str]) -> None:
    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        path.write_bytes(response.content)


async def estimate_size(client: httpx.AsyncClient, url: str) -> int:
    try:
        response = await client.head(url)
        if response.status_code < 400:
            return int(response.headers.get("Content-Length") or 0)
    except Exception:
        return 0
    return 0


manager = DownloadManager()
