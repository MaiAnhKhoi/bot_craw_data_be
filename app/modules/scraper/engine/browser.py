"""Quản lý phiên trình duyệt: khởi chạy, che dấu vết tự động hoá, tường consent,
phát hiện bị chặn.

Chạy được trên hai driver:
- `playwright`  : mặc định, ổn định, có sẵn image Docker chính thức.
- `patchright`  : bản vá drop-in của Playwright, che thêm các rò rỉ ở tầng CDP.
Đổi bằng biến môi trường BCD_BROWSER_ENGINE, không phải sửa code.
"""
from __future__ import annotations

import asyncio
import random
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from loguru import logger

from app.core.config import Settings


class BlockedError(RuntimeError):
    """Google trả trang 'unusual traffic' / captcha."""


class SuspiciousPageError(RuntimeError):
    """Trang tải được nhưng rỗng/thiếu — dấu hiệu bị hạ chất lượng phục vụ."""


# Vệ sinh dấu vân tay ở mức tối thiểu. Không thay thế được một bộ stealth đầy đủ,
# nhưng xoá các dấu hiệu tự động hoá lộ liễu nhất của Chromium đóng gói sẵn.
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
if (!window.chrome) { window.chrome = { runtime: {} }; }
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['vi-VN', 'vi', 'en-US'] });
"""

_CONSENT_BUTTON_RE = re.compile(
    r"^(Accept all|Reject all|I agree|Chấp nhận tất cả|Từ chối tất cả|Tôi đồng ý)$", re.IGNORECASE
)
_BLOCK_URL_MARKERS = ("/sorry/", "ipv4.google.com/sorry", "ipv6.google.com/sorry")
_BLOCK_TEXT_MARKERS = ("unusual traffic", "lưu lượng truy cập bất thường")
_STATIC_ASSET_RE = re.compile(r"[.](png|jpe?g|gif|webp|svg|ico|mp4|webm|woff2?|ttf|otf)([?].*)?$")
# Miền chỉ phục vụ đo đạc/quảng cáo — chặn luôn để giảm băng thông và thời gian tải.
_BLOCKED_HOSTS = (
    "googleadservices.com", "doubleclick.net", "google-analytics.com",
    "googletagmanager.com", "gstatic.com/generate_204",
)


def _load_driver(engine: str):  # noqa: ANN202
    """Nạp async_playwright của driver đã chọn, có đường lùi về playwright."""
    if engine == "patchright":
        try:
            from patchright.async_api import async_playwright  # type: ignore

            logger.info("Dùng driver patchright (chống nhận diện tốt hơn)")
            return async_playwright
        except ImportError:
            logger.warning("Chưa cài patchright, quay về playwright")
    from playwright.async_api import async_playwright

    return async_playwright


@asynccontextmanager
async def launch_context(settings: Settings) -> AsyncIterator[Any]:
    """Mở một BrowserContext đã sẵn sàng (hồ sơ bền để giữ cookie/consent).

    Hồ sơ bền rất quan trọng khi chạy dài ngày: phiên cũ trông giống một người
    dùng quen thuộc hơn là một khách lạ mới toanh mỗi lần mở.
    """
    async_playwright = _load_driver(settings.browser_engine)
    args = [
        "--disable-blink-features=AutomationControlled",
        f"--lang={settings.locale}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",   # bắt buộc trong Docker: /dev/shm mặc định chỉ 64MB
    ]
    profile = Path(settings.browser_profile_dir)
    profile.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(profile),
            headless=settings.headless,
            args=args,
            locale=settings.locale,
            timezone_id=settings.timezone,
            viewport={"width": settings.viewport_width, "height": settings.viewport_height},
            ignore_default_args=["--enable-automation"],
        )
        await context.add_init_script(_STEALTH_JS)
        try:
            yield context
        finally:
            await context.close()


async def _abort(route) -> None:  # noqa: ANN001
    await route.abort()


async def new_page(context, timeout_ms: int):  # noqa: ANN001, ANN201
    page = await context.new_page()
    page.set_default_timeout(timeout_ms)
    page.set_default_navigation_timeout(timeout_ms)
    # Chặn ảnh/font/video: đây là mức tăng tốc lớn nhất cho một luồng, và giảm
    # khoảng 60-70% băng thông.
    await page.route(_STATIC_ASSET_RE, _abort)
    for host in _BLOCKED_HOSTS:
        await page.route(re.compile(re.escape(host)), _abort)
    return page


async def human_delay(lo: float, hi: float) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def dismiss_consent(page) -> bool:  # noqa: ANN001
    """Bấm qua consent.google.com nếu gặp."""
    if "consent.google" not in page.url:
        return False
    try:
        button = page.get_by_role("button", name=_CONSENT_BUTTON_RE).first
        await button.wait_for(timeout=5_000)
        await button.click()
        await page.wait_for_load_state("domcontentloaded")
        logger.info("Đã bỏ qua tường consent của Google")
        return True
    except Exception:  # noqa: BLE001
        logger.warning("Gặp trang consent nhưng không thấy nút quen thuộc: {}", page.url)
        return False


async def raise_if_blocked(page) -> None:  # noqa: ANN001
    url = (page.url or "").lower()
    if any(m in url for m in _BLOCK_URL_MARKERS):
        raise BlockedError(f"Trang chặn của Google: {page.url}")
    try:
        title = (await page.title()).lower()
    except Exception:  # noqa: BLE001 — trang đang chuyển hướng
        return
    if any(m in title for m in _BLOCK_TEXT_MARKERS):
        raise BlockedError(f"Trang chặn của Google (tiêu đề): {title}")
    if await page.locator("form#captcha-form").count():
        raise BlockedError("Google yêu cầu giải captcha")


async def goto(page, url: str) -> None:  # noqa: ANN001
    """Điều hướng, xử lý consent, và dừng ngay nếu bị chặn."""
    await page.goto(url, wait_until="domcontentloaded")
    await dismiss_consent(page)
    await raise_if_blocked(page)


async def human_scroll(feed, pixels: int = 900) -> None:  # noqa: ANN001
    """Cuộn từng nấc như lăn chuột thay vì nhảy thẳng xuống đáy."""
    steps = random.randint(3, 5)
    for _ in range(steps):
        await feed.evaluate(f"el => el.scrollBy(0, {pixels // steps})")
        await asyncio.sleep(random.uniform(0.12, 0.3))
