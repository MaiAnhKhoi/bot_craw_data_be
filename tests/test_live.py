"""Test chạm Google Maps thật — chốt chặn phát hiện Google đổi DOM.

Bỏ qua trừ khi đặt GMAPS_LIVE=1. Chạy hằng tuần, hoặc bất cứ khi nào
`/stats/overview` cho thấy độ đầy đủ của một cột rơi đột ngột:

    GMAPS_LIVE=1 pytest tests/test_live.py -q

Khi test này đỏ, chỗ cần sửa nằm gọn trong hai file:
`engine/search.py` (thẻ kết quả) và `engine/detail.py` (trang chi tiết).
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.core.config import Settings
from app.modules.scraper.engine.browser import launch_context, new_page
from app.modules.scraper.engine.detail import scrape_detail
from app.modules.scraper.engine.search import search_query

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("GMAPS_LIVE") != "1", reason="đặt GMAPS_LIVE=1 để chạy"),
]

QUERY = "cửa hàng trái cây quận 1"


@pytest.fixture(scope="module")
def live_data():
    settings = Settings(
        headless=True,
        browser_profile_dir="var/browser_profile_test",
        scroll_pause_min=1.0,
        scroll_pause_max=1.8,
    )

    async def run():
        async with launch_context(settings) as ctx:
            page = await new_page(ctx, settings.page_timeout_ms)
            cards = await search_query(page, QUERY, settings, max_results=12)
            details = []
            for card in cards[:3]:
                details.append(await scrape_detail(page, card.maps_url))
                await asyncio.sleep(2)
            return cards, details

    return asyncio.run(run())


def test_the_ket_qua_van_cho_du_du_lieu(live_data):
    """Thẻ kết quả phải còn cho sẵn tên + SĐT + danh mục — nền tảng tốc độ của hệ thống."""
    cards, _ = live_data
    assert len(cards) >= 5, "danh sách trả quá ít kết quả — selector feed có thể đã đổi"
    assert all(c.feature_id for c in cards), "href không còn chứa feature_id"
    assert all(c.lat and c.lng for c in cards), "href không còn chứa toạ độ"
    assert all(c.name for c in cards)

    with_phone = sum(1 for c in cards if c.phone_raw)
    with_category = sum(1 for c in cards if c.category)
    with_address = sum(1 for c in cards if c.address_short)
    # Không đòi 100%: vài nơi thật sự không công bố SĐT. Nhưng dưới một nửa thì
    # gần như chắc chắn là bộ bóc tách thẻ đã hỏng, không phải dữ liệu thiếu.
    assert with_phone >= len(cards) // 2, f"chỉ {with_phone}/{len(cards)} thẻ có SĐT"
    assert with_category >= len(cards) // 2, f"chỉ {with_category}/{len(cards)} thẻ có danh mục"
    assert with_address >= len(cards) // 2, f"chỉ {with_address}/{len(cards)} thẻ có địa chỉ"


def test_trang_chi_tiet_van_cho_du_4_truong(live_data):
    _, details = live_data
    assert len(details) == 3
    for d in details:
        assert d.name, "selector h1 đã đổi"
        assert d.address, f"{d.name}: selector địa chỉ đã đổi"
        assert d.feature_id and d.lat and d.lng

    # Website là trường duy nhất chỉ có ở trang chi tiết; ba cửa hàng ở Quận 1 mà
    # không có nổi một cái nào thì nghi selector `a[data-item-id="authority"]` hỏng.
    assert any(d.website for d in details), "selector website đã đổi"
    assert any(d.phone_raw for d in details), "selector SĐT đã đổi"
    assert any(d.category for d in details), "selector danh mục đã đổi"
    # Tín hiệu nuôi bộ chấm sống/chết
    assert any(d.has_hours for d in details), "selector giờ mở cửa đã đổi"
    assert any(d.review_count for d in details), "selector số đánh giá đã đổi"
    assert any(d.latest_review_days is not None for d in details), "không đọc được tuổi đánh giá"
