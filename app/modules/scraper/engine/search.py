"""Pha tìm kiếm: từ khoá -> cuộn hết danh sách -> danh sách CardResult.

Điểm khác biệt về tốc độ so với cách làm thông thường: ta bóc luôn nội dung THẺ
kết quả (tên, SĐT, danh mục, địa chỉ rút gọn, rating, trạng thái mở cửa) ngay
trong pha này. Một lượt `evaluate` lấy hết cả trang danh sách, nên chi phí gần
bằng không — trong khi mở từng trang chi tiết tốn 1,5-2 giây mỗi địa điểm.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from loguru import logger

from app.core.config import Settings
from app.modules.scraper.engine.browser import (
    goto,
    human_delay,
    human_scroll,
    raise_if_blocked,
)
from app.modules.scraper.engine.models import (
    STOP_CAP,
    STOP_CUT_OFF,
    STOP_EMPTY,
    STOP_EXHAUSTED,
    STOP_UNKNOWN,
    CardResult,
    SearchOutcome,
)
from app.modules.scraper.engine.parsers import normalize_ws, parse_card, parse_place_url

_END_OF_LIST_RE = re.compile(
    r"(đã xem hết danh sách|reached the end of the list|không có kết quả nào khác)", re.I
)
_NO_RESULTS_RE = re.compile(r"(không tìm thấy|can't find|no results)", re.I)

# Một lượt duyệt lấy toàn bộ thẻ đang render trong feed.
_COLLECT_JS = r"""
(feed) => {
  const out = [];
  for (const child of Array.from(feed.children)) {
    const a = child.querySelector('a[href*="/maps/place/"]');
    if (!a) continue;
    out.push({
      href: a.href,
      name: a.getAttribute('aria-label'),
      lines: (child.innerText || '').split('\n').map(s => s.trim()).filter(Boolean),
      ratingLabels: Array.from(child.querySelectorAll('[role="img"]'))
        .map(e => e.getAttribute('aria-label')).filter(Boolean),
    });
  }
  return out;
}
"""


def build_search_url(query: str, settings: Settings, hl: str | None = None, gl: str | None = None) -> str:
    """`hl`/`gl` của CHÍNH truy vấn này, không phải của cả job.

    `gl` quyết định Google ưu tiên kết quả ở nước nào. Đây không phải chi tiết nhỏ:
    tìm "fruit wholesaler Bangkok" với gl=vn trả về cửa hàng ở TP.HCM.
    """
    return f"https://www.google.com/maps/search/{quote(query)}?hl={hl or settings.hl}&gl={gl or settings.gl}"


def card_from_raw(raw: dict, region: str = "VN") -> CardResult | None:
    """Ghép dữ liệu DOM thô của một thẻ thành CardResult.

    `region` là quốc gia đang quét (`gl` của truy vấn). Bắt buộc phải truyền
    xuống: bộ nhận diện số điện thoại cần biết vùng để đọc đúng số nội địa,
    và để không nhận nhầm số nhà trong địa chỉ thành số điện thoại.
    """
    href = raw.get("href") or ""
    info = parse_place_url(href)
    name = normalize_ws(raw.get("name")) or info["name"]
    if not name:
        return None
    parsed = parse_card(name, raw.get("lines"), raw.get("ratingLabels"), region)
    return CardResult(
        name=name,
        maps_url=href,
        feature_id=info["feature_id"],
        cid=info["cid"],
        lat=info["lat"],
        lng=info["lng"],
        category=parsed["category"],
        address_short=parsed["address_short"],
        phone_raw=parsed["phone_raw"],
        rating=parsed["rating"],
        review_count=parsed["review_count"],
        business_status=parsed["business_status"],
        hours_summary=parsed["hours_summary"],
        has_hours=parsed["has_hours"],
        sponsored=parsed["sponsored"],
    )


async def search_query(  # noqa: ANN001
    page,
    query: str,
    settings: Settings,
    max_results: int = 200,
    hl: str | None = None,
    gl: str | None = None,
) -> SearchOutcome:
    """Chạy một truy vấn, cuộn tới hết danh sách, trả các thẻ đã khử trùng lặp.

    Trả kèm LÝ DO DỪNG (xem `models.STOP_*`): đó là thứ duy nhất phân biệt được
    "địa bàn này đã quét sạch" với "Google cắt giữa chừng, còn sót".
    """
    region = (gl or settings.gl or "VN").upper()
    await goto(page, build_search_url(query, settings, hl, gl))
    feed = page.locator('div[role="feed"]').first
    try:
        await feed.wait_for(timeout=15_000)
    except Exception:  # noqa: BLE001 — Playwright ném TimeoutError riêng của nó
        # Google nhảy thẳng vào trang chi tiết khi truy vấn khớp đúng một doanh nghiệp.
        if "/maps/place/" in page.url:
            card = card_from_raw({"href": page.url, "lines": [], "ratingLabels": []}, region)
            if card:
                try:
                    heading = page.locator('div[role="main"] h1').first
                    card.name = normalize_ws(await heading.inner_text(timeout=5_000)) or card.name
                except Exception:  # noqa: BLE001
                    pass
                logger.info("[{}] khớp đúng 1 địa điểm", query)
                # Google nhảy thẳng vào trang chi tiết = chỉ có đúng một nơi khớp,
                # không phải bị cắt giữa chừng.
                return SearchOutcome([card], STOP_EXHAUSTED)
        body = (await page.locator("body").inner_text())[:300]
        if _NO_RESULTS_RE.search(body):
            logger.info("[{}] không có kết quả", query)
            return SearchOutcome([], STOP_EMPTY)
        await raise_if_blocked(page)
        logger.warning("[{}] danh sách kết quả không hiện ra (url={})", query, page.url)
        return SearchOutcome([], STOP_UNKNOWN)

    seen: dict[str, CardResult] = {}
    idle_rounds, scrolls = 0, 0
    stop_reason = STOP_CUT_OFF   # thoát vì hết `idle_rounds` thì rơi vào đây
    while len(seen) < max_results and idle_rounds < 5:
        before = len(seen)
        for raw in await feed.evaluate(_COLLECT_JS):
            card = card_from_raw(raw, region)
            if card is None:
                continue
            seen.setdefault(card.feature_id or card.maps_url, card)
        if len(seen) >= max_results:
            stop_reason = STOP_CAP
            break
        if await feed.get_by_text(_END_OF_LIST_RE).count():
            logger.debug("[{}] chạm dấu hết danh sách sau {} lần cuộn", query, scrolls)
            stop_reason = STOP_EXHAUSTED
            break

        idle_rounds = idle_rounds + 1 if len(seen) == before else 0
        if idle_rounds:
            # Bộ nạp lười của Google đôi khi cần một cú hích: lùi lên rồi xuống lại.
            await feed.evaluate("el => el.scrollBy(0, -300)")
            await human_delay(0.3, 0.6)
        await human_scroll(feed, pixels=1400)
        await feed.evaluate("el => el.scrollTo(0, el.scrollHeight)")
        scrolls += 1
        backoff = 1 + idle_rounds
        await human_delay(settings.scroll_pause_min * backoff, settings.scroll_pause_max * backoff)
        if scrolls % 10 == 0:
            await raise_if_blocked(page)

    cards = list(seen.values())[:max_results]
    if not cards:
        # Feed hiện ra nhưng không đọc được thẻ nào: coi như rỗng, đừng báo "bị cắt"
        # rồi đẩy người dùng đi chia nhỏ địa bàn một cách vô ích.
        stop_reason = STOP_EMPTY
    with_phone = sum(1 for c in cards if c.phone_raw)
    logger.info(
        "[{}] {} địa điểm ({} lần cuộn, dừng vì {}) — {} đã có SĐT ngay từ thẻ",
        query,
        len(cards),
        scrolls,
        stop_reason,
        with_phone,
    )
    return SearchOutcome(cards, stop_reason)
