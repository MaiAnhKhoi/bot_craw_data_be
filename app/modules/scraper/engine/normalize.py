"""Chuẩn hoá số điện thoại và URL website."""
from __future__ import annotations

import re

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

_SPLIT_RE = re.compile(r"\s*(?:/|,|;|\bhoặc\b|\bor\b)\s*", re.IGNORECASE)

# Tên miền Google dùng cho link nội bộ / đặt chỗ — không phải website của doanh nghiệp.
_NOT_A_COMPANY_SITE = (
    "google.com", "google.com.vn", "goo.gl", "maps.app.goo.gl", "business.site",
)


def normalize_phone(raw: str | None, region: str = "VN") -> tuple[str | None, str | None, bool]:
    """Trả (e164, dạng quốc gia, có hợp lệ không) cho số đầu tiên đọc được.

    Google đôi khi liệt kê nhiều số ("028 1234 5678 / 0901 234 567"); ta lấy số
    hợp lệ đầu tiên và vẫn giữ nguyên chuỗi gốc ở `phone_raw`.
    """
    if not raw:
        return None, None, False
    candidates = [c for c in _SPLIT_RE.split(raw) if c and re.search(r"\d", c)]
    if not candidates:
        candidates = [raw]
    first_parsed = None
    for cand in candidates:
        try:
            parsed = phonenumbers.parse(cand, region)
        except NumberParseException:
            continue
        if first_parsed is None:
            first_parsed = parsed
        if phonenumbers.is_valid_number(parsed):
            return (
                phonenumbers.format_number(parsed, PhoneNumberFormat.E164),
                phonenumbers.format_number(parsed, PhoneNumberFormat.NATIONAL),
                True,
            )
    if first_parsed is not None:
        # vẫn giữ E.164 tạm để người dùng tự nhìn, nhưng đánh dấu không hợp lệ
        return (
            phonenumbers.format_number(first_parsed, PhoneNumberFormat.E164),
            phonenumbers.format_number(first_parsed, PhoneNumberFormat.NATIONAL),
            False,
        )
    return None, None, False


def clean_company_website(url: str | None) -> str | None:
    """Giữ lại URL website RIÊNG của doanh nghiệp; loại link nội bộ của Google."""
    if not url:
        return None
    url = url.strip()
    if not url or url.startswith("/"):
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    host = re.sub(r"^https?://", "", url).split("/", 1)[0].split(":", 1)[0].lower()
    host = host[4:] if host.startswith("www.") else host
    if not host or "." not in host:
        return None
    if any(host == bad or host.endswith("." + bad) for bad in _NOT_A_COMPANY_SITE):
        return None
    return url


def website_host(url: str | None) -> str | None:
    if not url:
        return None
    host = re.sub(r"^https?://", "", url).split("/", 1)[0].split(":", 1)[0].lower()
    return host or None
