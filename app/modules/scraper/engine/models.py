"""Cấu trúc dữ liệu engine trả ra (thuần Python, không dính ORM)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CardResult:
    """Dữ liệu đọc thẳng từ thẻ trong danh sách kết quả — KHÔNG mở trang chi tiết.

    Thẻ cho sẵn 3/4 trường nghiệp vụ (tên, vị trí rút gọn, SĐT). Website thì không,
    nên chỉ khi cần website mới phải mở trang chi tiết.
    """

    name: str
    maps_url: str
    feature_id: str | None = None
    cid: str | None = None
    lat: float | None = None
    lng: float | None = None
    category: str | None = None
    address_short: str | None = None
    phone_raw: str | None = None
    rating: float | None = None
    review_count: int | None = None
    business_status: str = "OPERATIONAL"
    hours_summary: str | None = None
    has_hours: bool = False
    sponsored: bool = False


@dataclass
class DetailResult:
    """Dữ liệu đọc từ trang chi tiết của một địa điểm."""

    name: str | None = None
    address: str | None = None
    phone_raw: str | None = None
    website: str | None = None            # website RIÊNG của doanh nghiệp
    category: str | None = None
    rating: float | None = None
    review_count: int | None = None
    latest_review_days: int | None = None
    business_status: str = "OPERATIONAL"
    hours_summary: str | None = None
    has_hours: bool = False
    lat: float | None = None
    lng: float | None = None
    feature_id: str | None = None
    cid: str | None = None
    missing_fields: list[str] = field(default_factory=list)
