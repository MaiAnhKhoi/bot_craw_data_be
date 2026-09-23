"""Pha chi tiết: mở trang một địa điểm và bóc ra DetailResult.

Mọi thao tác đọc DOM gói trong MỘT lần `page.evaluate` (EXTRACT_JS) nên mỗi địa
điểm chỉ tốn một vòng gọi; đồng thời logic bóc tách test được offline bằng
`page.set_content` với file HTML mẫu.

Thứ tự ưu tiên selector:
  data-item-id="..."      bền nhiều năm, không phụ thuộc ngôn ngữ  (tốt nhất)
  aria-label / role       bền, nhưng chữ theo ngôn ngữ (parsers.py lo)
  class bị rút gọn        có thể đổi bất cứ lúc nào — chỉ dùng làm đường lùi cuối
"""
from __future__ import annotations

from loguru import logger

from app.modules.scraper.engine.browser import goto
from app.modules.scraper.engine.models import DetailResult
from app.modules.scraper.engine.normalize import clean_company_website
from app.modules.scraper.engine.parsers import (
    detect_business_status,
    newest_review_days,
    normalize_ws,
    parse_hours_summary,
    parse_phone_item_id,
    parse_place_url,
    parse_rating,
    parse_review_count,
    strip_label,
)


class DetailParseError(RuntimeError):
    """Khung chi tiết không hiện ra (bị đẩy về danh sách, trang rỗng...)."""


EXTRACT_JS = r"""
() => {
  const main = document.querySelector('div[role="main"]') || document.body;
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim() || null;
  const q = (sel) => main.querySelector(sel);
  const text = (el) => el ? clean(el.innerText || el.textContent) : null;
  const attr = (sel, a) => { const el = q(sel); return el ? el.getAttribute(a) : null; };
  const labels = (sel) => Array.from(main.querySelectorAll(sel))
      .map(e => e.getAttribute('aria-label')).filter(Boolean);

  const phoneBtn = q('button[data-item-id^="phone:tel:"]');

  // Giờ mở cửa hiện theo HAI kiểu: bảng nội tuyến, hoặc nút tóm tắt "oh" khi
  // doanh nghiệp có nhiều loại giờ (giao hàng / mang đi / tại chỗ).
  let hoursRows = 0;
  main.querySelectorAll('table tr').forEach(tr => {
    const tds = tr.querySelectorAll('td');
    if (tds.length < 2) return;
    const day = clean(tds[0].textContent);
    if (!day || /^\d+$/.test(day)) return;   // bỏ bảng biểu đồ sao 5..1
    if (clean(tds[1].textContent)) hoursRows += 1;
  });

  // Tuổi của các đánh giá hiển thị sẵn trong khung — không cần bấm sang tab đánh giá.
  const reviewAges = Array.from(main.querySelectorAll('span, div'))
      .map(e => (e.childElementCount === 0 ? (e.textContent || '').trim() : ''))
      .filter(t => t.length > 0 && t.length < 32 && /(trước|ago)$/i.test(t))
      .slice(0, 25);

  return {
    name: text(q('h1')),
    phone_item_id: phoneBtn ? phoneBtn.getAttribute('data-item-id') : null,
    phone_label: phoneBtn ? phoneBtn.getAttribute('aria-label') : null,
    website: attr('a[data-item-id="authority"]', 'href'),
    address_label: attr('button[data-item-id="address"]', 'aria-label'),
    oh_label: attr('button[data-item-id="oh"]', 'aria-label'),
    category: text(q('button[jsaction*="category"]')),
    rating_label: labels('span[role="img"]').find(l => /\d/.test(l) && /(star|sao)/i.test(l)) || null,
    review_label: labels('[aria-label]').find(
        l => /^\s*[\d.,\s]+\s*[KNM]?\s*(reviews?|bài đánh giá|đánh giá)\s*$/i.test(l)) || null,
    hours_rows: hoursRows,
    review_ages: reviewAges,
    header_text: (main.innerText || '').slice(0, 1200),
    url: location.href,
  };
}
"""

# Các trường ta theo dõi để phát hiện Google đổi DOM: nếu tỉ lệ thiếu tăng vọt
# giữa hai lần chạy thì selector đã hỏng, không phải dữ liệu thật thiếu.
_MONITORED_FIELDS = ("name", "address", "category")


def build_detail(raw: dict) -> DetailResult:
    """Biến payload của EXTRACT_JS thành DetailResult (hàm thuần, có unit test)."""
    url_info = parse_place_url(raw.get("url") or "")
    phone_raw = parse_phone_item_id(raw.get("phone_item_id")) or strip_label(raw.get("phone_label"), "phone")
    hours_summary = parse_hours_summary(raw.get("oh_label"))
    has_hours = bool(raw.get("hours_rows")) or bool(hours_summary)

    detail = DetailResult(
        name=normalize_ws(raw.get("name")),
        address=strip_label(raw.get("address_label"), "address"),
        phone_raw=phone_raw,
        website=clean_company_website(raw.get("website")),
        category=normalize_ws(raw.get("category")),
        rating=parse_rating(raw.get("rating_label")),
        review_count=parse_review_count(raw.get("review_label")),
        latest_review_days=newest_review_days(raw.get("review_ages")),
        business_status=detect_business_status(raw.get("header_text")),
        hours_summary=hours_summary,
        has_hours=has_hours,
        lat=url_info["lat"],
        lng=url_info["lng"],
        feature_id=url_info["feature_id"],
        cid=url_info["cid"],
    )
    detail.missing_fields = [f for f in _MONITORED_FIELDS if not getattr(detail, f)]
    return detail


async def scrape_detail(page, url: str, timeout_ms: int = 20_000) -> DetailResult:  # noqa: ANN001
    await goto(page, url)
    try:
        await page.locator('div[role="main"] h1').first.wait_for(timeout=timeout_ms)
    except Exception as exc:  # noqa: BLE001
        raise DetailParseError(f"khung chi tiết không hiện ra: {page.url}") from exc
    # Nút SĐT/địa chỉ render trễ hơn tiêu đề một nhịp; chờ ngắn rồi đọc.
    try:
        await page.locator(
            'div[role="main"] button[data-item-id="address"], '
            'div[role="main"] button[data-item-id^="phone:tel:"]'
        ).first.wait_for(timeout=4_000)
    except Exception:  # noqa: BLE001
        pass

    detail = build_detail(await page.evaluate(EXTRACT_JS))
    if not detail.name:
        raise DetailParseError(f"không đọc được tên tại {page.url}")
    if detail.missing_fields:
        logger.debug("{} thiếu trường {}", detail.name, detail.missing_fields)
    return detail
