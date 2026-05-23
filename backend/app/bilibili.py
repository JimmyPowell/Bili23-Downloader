from __future__ import annotations

import base64
import io
import re
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlencode

import httpx
import qrcode

from .database import connect


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def load_cookies() -> dict[str, str]:
    with connect() as db:
        row = db.execute("SELECT cookies FROM bili_sessions WHERE id = 1").fetchone()
    if not row:
        return {}
    import json

    return json.loads(row["cookies"] or "{}")


def save_cookies(cookies: dict[str, str]) -> None:
    import json
    import time

    with connect() as db:
        db.execute(
            "UPDATE bili_sessions SET cookies = ?, updated_at = ? WHERE id = 1",
            (json.dumps(cookies, ensure_ascii=False), int(time.time())),
        )


class BiliError(RuntimeError):
    pass


class BiliClient:
    def __init__(self) -> None:
        self.cookies = load_cookies()

    def headers(self, referer: str = "https://www.bilibili.com/") -> dict[str, str]:
        headers = {"User-Agent": UA, "Referer": referer}
        if self.cookies:
            headers["Cookie"] = _cookie_header(self.cookies)
        return headers

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(headers=self.headers(), timeout=20, follow_redirects=True) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        code = data.get("code", 0)
        if code != 0:
            raise BiliError(data.get("message") or data.get("msg") or f"Bilibili API error {code}")
        return data

    async def account(self) -> dict[str, Any]:
        data = await self.get_json("https://api.bilibili.com/x/web-interface/nav")
        nav = data.get("data", {})
        return {
            "is_login": bool(nav.get("isLogin")),
            "uname": nav.get("uname", ""),
            "mid": nav.get("mid", 0),
            "face": nav.get("face", ""),
            "level": nav.get("level_info", {}).get("current_level", 0),
        }

    async def qrcode_start(self) -> dict[str, str]:
        data = await self.get_json("https://passport.bilibili.com/x/passport-login/web/qrcode/generate")
        payload = data["data"]
        img = qrcode.make(payload["url"])
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        import time

        with connect() as db:
            db.execute(
                "UPDATE bili_sessions SET qr_key = ?, qr_url = ?, updated_at = ? WHERE id = 1",
                (payload["qrcode_key"], payload["url"], int(time.time())),
            )
        return {
            "qrcode_key": payload["qrcode_key"],
            "url": payload["url"],
            "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
        }

    async def qrcode_status(self) -> dict[str, Any]:
        with connect() as db:
            row = db.execute("SELECT qr_key FROM bili_sessions WHERE id = 1").fetchone()
        key = row["qr_key"] if row else ""
        if not key:
            raise BiliError("No active QR login")
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20, follow_redirects=False) as client:
            response = await client.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                params={"qrcode_key": key},
            )
            response.raise_for_status()
            data = response.json()
            code = data.get("data", {}).get("code")
            if code == 0:
                parsed: dict[str, str] = {}
                for cookie in response.headers.get_list("set-cookie"):
                    jar = SimpleCookie()
                    jar.load(cookie)
                    for name, morsel in jar.items():
                        parsed[name] = morsel.value
                if parsed:
                    save_cookies(parsed)
        return data.get("data", data)

    async def parse_url(self, url: str) -> dict[str, Any]:
        bvid = self.extract_bvid(url)
        data = await self.get_json("https://api.bilibili.com/x/web-interface/view", {"bvid": bvid})
        info = data["data"]
        episodes = []
        for page in info.get("pages", []):
            episodes.append(
                {
                    "title": page.get("part") or info.get("title", ""),
                    "part": page.get("page", 1),
                    "aid": info.get("aid", 0),
                    "bvid": info.get("bvid", bvid),
                    "cid": page.get("cid", info.get("cid", 0)),
                    "duration": page.get("duration", 0),
                    "url": f"https://www.bilibili.com/video/{info.get('bvid', bvid)}?p={page.get('page', 1)}",
                    "cover": info.get("pic", ""),
                    "uploader": info.get("owner", {}).get("name", ""),
                    "pubtime": info.get("pubdate", 0),
                }
            )
        return {
            "type": "video",
            "title": info.get("title", ""),
            "bvid": info.get("bvid", bvid),
            "aid": info.get("aid", 0),
            "cover": info.get("pic", ""),
            "uploader": info.get("owner", {}).get("name", ""),
            "description": info.get("desc", ""),
            "episodes": episodes,
        }

    def extract_bvid(self, url: str) -> str:
        bvid = re.search(r"BV[0-9A-Za-z]+", url)
        if bvid:
            return bvid.group(0)
        raise BiliError("当前服务器版已支持 BV/视频链接解析，番剧/收藏夹会在同一 API 层继续扩展")

    async def playurl(self, bvid: str, cid: int, qn: int) -> dict[str, Any]:
        params = {"bvid": bvid, "cid": cid, "qn": qn, "fnver": 0, "fnval": 4048, "fourk": 1}
        data = await self.get_json("https://api.bilibili.com/x/player/playurl", params)
        return data["data"]

    async def danmaku_xml(self, cid: int) -> bytes:
        async with httpx.AsyncClient(headers=self.headers(), timeout=20) as client:
            response = await client.get(f"https://comment.bilibili.com/{cid}.xml")
            response.raise_for_status()
            return response.content
