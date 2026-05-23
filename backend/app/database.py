from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .config import DB_PATH, DEFAULT_SETTINGS, ensure_dirs


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bili_sessions (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cookies TEXT NOT NULL DEFAULT '{}',
                qr_key TEXT NOT NULL DEFAULT '',
                qr_url TEXT NOT NULL DEFAULT '',
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                bvid TEXT NOT NULL,
                aid INTEGER NOT NULL,
                cid INTEGER NOT NULL,
                part INTEGER NOT NULL,
                cover TEXT NOT NULL,
                status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                speed INTEGER NOT NULL DEFAULT 0,
                downloaded_size INTEGER NOT NULL DEFAULT 0,
                total_size INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                output_dir TEXT NOT NULL DEFAULT '',
                output_file TEXT NOT NULL DEFAULT '',
                options TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                completed_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS batch_jobs (
                id TEXT PRIMARY KEY,
                source_url TEXT NOT NULL,
                status TEXT NOT NULL,
                total INTEGER NOT NULL DEFAULT 0,
                created INTEGER NOT NULL DEFAULT 0,
                current_page INTEGER NOT NULL DEFAULT 1,
                page_size INTEGER NOT NULL DEFAULT 30,
                total_pages INTEGER NOT NULL DEFAULT 1,
                total_items INTEGER NOT NULL DEFAULT 0,
                completed_pages INTEGER NOT NULL DEFAULT 0,
                options TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            """
        )
        _ensure_columns(db, "batch_jobs", {
            "current_page": "INTEGER NOT NULL DEFAULT 1",
            "page_size": "INTEGER NOT NULL DEFAULT 30",
            "total_pages": "INTEGER NOT NULL DEFAULT 1",
            "total_items": "INTEGER NOT NULL DEFAULT 0",
            "completed_pages": "INTEGER NOT NULL DEFAULT 0",
            "options": "TEXT NOT NULL DEFAULT '{}'",
        })
        for key, value in DEFAULT_SETTINGS.items():
            db.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )
        db.execute(
            "INSERT OR IGNORE INTO bili_sessions(id, updated_at) VALUES(1, ?)",
            (int(time.time()),),
        )


def _ensure_columns(db: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def read_settings() -> dict[str, Any]:
    with connect() as db:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: json.loads(row["value"]) for row in rows}


def write_settings(values: dict[str, Any]) -> dict[str, Any]:
    with connect() as db:
        for key, value in values.items():
            db.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )
    return read_settings()


def log(level: str, message: str, task_id: str | None = None) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO logs(task_id, level, message, created_at) VALUES(?, ?, ?, ?)",
            (task_id, level, message, int(time.time())),
        )


def safe_child(root: str | Path, *parts: str) -> Path:
    root_path = Path(root).expanduser().resolve()
    child = root_path.joinpath(*parts).resolve()
    if root_path != child and root_path not in child.parents:
        raise ValueError("Invalid path outside download directory")
    return child
