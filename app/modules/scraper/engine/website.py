"""Kiểm tra website doanh nghiệp còn sống không — bằng httpx, KHÔNG dùng trình duyệt.

Đây là tín hiệu rẻ nhất để phát hiện công ty đã ngừng hoạt động: tên miền hết hạn,
máy chủ chết, hoặc trang đã bị "đỗ" (parking page của nhà đăng ký tên miền).
Chạy song song nhiều URL vì không tốn trình duyệt và không đụng tới Google.
"""
from __future__ import annotations

import asyncio
import re

import httpx
from loguru import logger

OK = "OK"
DEAD = "DEAD"
PARKED = "PARKED"
UNCHECKED = "UNCHECKED"
NONE = "NONE"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# Dấu hiệu trang "đỗ" / rao bán tên miền / hosting mặc định.
_PARKED_PATTERNS = re.compile(
    r"(tên miền này đang được rao bán|domain (?:is )?for sale|buy this domain"
    r"|parked (?:free )?(?:at|by)|this domain is parked|coming soon"
    r"|website đang được xây dựng|under construction|default web site page"
    r"|apache2? (?:ubuntu|debian) default page|welcome to nginx|it works!)",
    re.IGNORECASE,
)


async def check_one(client: httpx.AsyncClient, url: str) -> str:
    """Trả OK | DEAD | PARKED cho một URL."""
    try:
        resp = await client.get(url)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TooManyRedirects):
        return DEAD
    except httpx.HTTPError:
        return DEAD
    except Exception as exc:  # noqa: BLE001 — mạng có đủ kiểu lỗi lạ, không để vỡ job
        logger.debug("Kiểm tra website {} lỗi lạ: {}", url, exc)
        return DEAD

    if resp.status_code >= 500 or resp.status_code in (404, 410):
        return DEAD
    if resp.status_code >= 400:
        # 401/403 thường là tường lửa/bot-check, không có nghĩa là công ty đã chết.
        return OK

    body = ""
    ctype = resp.headers.get("content-type", "")
    if "text/html" in ctype or "text" in ctype:
        body = resp.text[:20_000]
    if body and _PARKED_PATTERNS.search(body):
        return PARKED
    if body and len(re.sub(r"<[^>]+>", "", body).strip()) < 120:
        # Trang gần như trống rỗng: coi như đỗ, không phải website đang vận hành.
        return PARKED
    return OK


async def check_many(urls: list[str], timeout: float = 8.0, concurrency: int = 8) -> dict[str, str]:
    """Kiểm tra nhiều website song song. Trả {url: trạng thái}."""
    urls = [u for u in dict.fromkeys(urls) if u]
    if not urls:
        return {}
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, str] = {}

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        limits=limits,
        headers={"User-Agent": _UA, "Accept-Language": "vi,en;q=0.8"},
        verify=False,   # rất nhiều site doanh nghiệp VN dùng chứng chỉ hết hạn nhưng vẫn sống
    ) as client:

        async def run(url: str) -> None:
            async with sem:
                results[url] = await check_one(client, url)

        await asyncio.gather(*(run(u) for u in urls))
    return results
