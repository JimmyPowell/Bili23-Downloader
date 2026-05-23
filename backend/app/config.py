from __future__ import annotations

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DOWNLOAD_DIR = BASE_DIR.parent / "downloads"
DB_PATH = DATA_DIR / "bili23-web.sqlite3"

DEFAULT_SETTINGS = {
    "download_dir": str(DOWNLOAD_DIR),
    "max_concurrent": 2,
    "chunk_size": 1024 * 512,
    "speed_limit_kbps": 0,
    "video_quality": 80,
    "audio_quality": 30280,
    "video_codec": "auto",
    "merge_av": True,
    "keep_parts": False,
    "download_cover": True,
    "download_danmaku": True,
    "download_metadata": True,
    "download_subtitle": True,
    "filename_template": "{title}-{part}",
    "admin_username": "admin",
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
