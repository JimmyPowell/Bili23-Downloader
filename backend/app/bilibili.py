from __future__ import annotations

import base64
import io
import re
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

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
        if bvid := self.extract_bvid(url, check=False):
            return await self.parse_video(bvid)
        if mid := self.extract_mid(url):
            return await self.parse_space(url, mid)
        raise BiliError("当前服务器版支持 BV/视频链接和 B 站个人主页链接解析")

    async def parse_video(self, bvid: str) -> dict[str, Any]:
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

    async def parse_space(self, url: str, mid: str) -> dict[str, Any]:
        parsed_url = urlparse(url)
        query = parse_qs(parsed_url.query)
        pn = max(1, self._int_query(query, "pn", 1))
        ps = min(50, max(1, self._int_query(query, "ps", 30)))
        archives = await self.space_archives(mid, pn, ps)
        card = await self.space_card(mid)
        videos = archives.get("archives", [])
        episodes = []
        for index, video in enumerate(videos, start=1):
            bvid = video.get("bvid", "")
            if not bvid:
                continue
            parsed_video = await self.parse_video(bvid)
            for episode in parsed_video["episodes"]:
                episode["part"] = index if len(parsed_video["episodes"]) == 1 else episode["part"]
                episode["source_title"] = video.get("title") or parsed_video["title"]
                episode["space_mid"] = int(mid)
                episodes.append(episode)
        page = archives.get("page") or {}
        total = int(page.get("total") or len(videos))
        owner = card.get("name") or f"UID {mid}"
        return {
            "type": "space",
            "title": f"{owner} 的投稿视频",
            "bvid": "",
            "aid": 0,
            "cover": card.get("face", "") or (episodes[0]["cover"] if episodes else ""),
            "uploader": owner,
            "description": card.get("sign", ""),
            "episodes": episodes,
            "pagination": {
                "current_page": pn,
                "page_size": ps,
                "total_items": total,
                "total_pages": (total + ps - 1) // ps if ps else 1,
            },
        }

    async def space_archives(self, mid: str, pn: int, ps: int) -> dict[str, Any]:
        params = {"mid": mid, "keywords": "", "pn": pn, "ps": ps}
        data = await self.get_json("https://api.bilibili.com/x/series/recArchivesByKeywords", params)
        return data["data"]

    async def space_card(self, mid: str) -> dict[str, Any]:
        data = await self.get_json("https://api.bilibili.com/x/web-interface/card", {"mid": mid})
        return data.get("data", {}).get("card", {})

    def extract_bvid(self, url: str, check: bool = True) -> str:
        bvid = re.search(r"BV[0-9A-Za-z]+", url)
        if bvid:
            return bvid.group(0)
        if check:
            raise BiliError("当前服务器版支持 BV/视频链接和 B 站个人主页链接解析")
        return ""

    def extract_mid(self, url: str) -> str:
        match = re.search(r"(?:space\.bilibili\.com|bilibili\.com/space)/(\d+)", url)
        if match:
            return match.group(1)
        parsed_url = urlparse(url)
        query = parse_qs(parsed_url.query)
        if query.get("mid") and query["mid"][0].isdigit():
            return query["mid"][0]
        if url.strip().isdigit():
            return url.strip()
        return ""

    def _int_query(self, query: dict[str, list[str]], key: str, default: int) -> int:
        try:
            return int(query.get(key, [default])[0])
        except (TypeError, ValueError):
            return default

    async def playurl(self, bvid: str, cid: int, qn: int) -> dict[str, Any]:
        params = {"bvid": bvid, "cid": cid, "qn": qn, "fnver": 0, "fnval": 4048, "fourk": 1}
        data = await self.get_json("https://api.bilibili.com/x/player/playurl", params)
        return data["data"]

    async def danmaku_xml(self, cid: int) -> bytes:
        async with httpx.AsyncClient(headers=self.headers(), timeout=20) as client:
            response = await client.get(f"https://comment.bilibili.com/{cid}.xml")
            response.raise_for_status()
            return response.content
