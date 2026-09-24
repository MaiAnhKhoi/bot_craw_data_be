from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.modules.geo import service as geo
from app.modules.scraper.engine.normalize import phone_country, to_international
from app.modules.scraper.place.entity import Place


class CountryCountResponse(BaseModel):
    """Một dòng của ô chọn "Quốc gia": mã để lọc, tên để hiện, số lượng để người
    dùng biết trước chọn vào sẽ có bao nhiêu địa điểm."""

    code: str                    # ISO alpha-2, ví dụ 'TH'
    name: str                    # tên tiếng Việt, ví dụ 'Thái Lan'
    count: int

    @classmethod
    def of(cls, code: str, count: int) -> CountryCountResponse:
        # `country_name` trả lại chính mã khi gặp mã lạ nên `name` không bao giờ rỗng.
        return cls(code=code, name=geo.country_name(code) or code, count=count)


class QueryCountResponse(BaseModel):
    """Một dòng của ô chọn "Lượt tìm": chuỗi truy vấn đầy đủ + số địa điểm nó ra."""

    query: str                   # '<từ khoá> <địa điểm>', ví dụ 'fruit wholesaler Phuket, Thailand'
    count: int


class PlaceResponse(BaseModel):
    """4 trường nghiệp vụ đứng trước; phần còn lại phục vụ lọc và chấm sống/chết."""

    id: int
    name: str
    # ĐỊA CHỈ ĐẦY ĐỦ, hoặc null. Không bao giờ là một mẩu — xem entity.Place.
    address: str | None
    # Mẩu địa chỉ trên thẻ kết quả, chỉ dùng khi `address` còn null. Giao diện
    # PHẢI hiện nó kèm dấu hiệu chưa đầy đủ, đừng trộn vào cùng một ô như
    # địa chỉ thật.
    address_short: str | None
    country_code: str | None     # ISO alpha-2, ví dụ 'TH'
    country_name: str | None     # tên tiếng Việt, ví dụ 'Thái Lan'
    country_source: str | None   # address | coords | gl — xem entity.Place
    # Quốc gia mà SỐ ĐIỆN THOẠI thuộc về. Lệch với `country_code` thì giao
    # diện đánh dấu — không phải lỗi, mà là dấu hiệu đáng chú ý.
    phone_country_code: str | None
    phone: str | None            # dạng quốc tế có mã nước, vd '+66 2 281 9715'
    phone_e164: str | None
    phone_valid: bool | None
    website: str | None          # website RIÊNG của doanh nghiệp
    website_status: str

    category: str | None
    rating: float | None
    review_count: int | None
    business_status: str
    liveness_score: int
    liveness_label: str
    liveness_reasons: list[str]
    latest_review_days: int | None

    lat: float | None
    lng: float | None
    maps_url: str | None
    # Trạng thái CHĂM SÓC — khác hẳn `status` (quét) và `liveness_label` (sống/chết).
    contact_status: str          # new | called | interested | rejected
    contact_note: str | None
    contact_at: datetime | None
    keywords: list[str] = []
    detail_scraped: bool
    scraped_at: datetime | None
    last_verified_at: datetime | None

    @classmethod
    def of(cls, p: Place, keywords: list[str] | None = None) -> PlaceResponse:
        return cls(
            id=p.id,
            name=p.name,
            address=p.address,
            address_short=p.address_short,
            country_code=p.country_code,
            country_name=geo.country_name(p.country_code),
            country_source=p.country_source,
            phone_country_code=phone_country(p.phone_e164),
            phone=to_international(p.phone_e164, p.phone_national or p.phone_raw),
            phone_e164=p.phone_e164,
            phone_valid=p.phone_valid,
            website=p.website,
            website_status=p.website_status,
            category=p.category,
            rating=p.rating,
            review_count=p.review_count,
            business_status=p.business_status,
            liveness_score=p.liveness_score,
            liveness_label=p.liveness_label,
            liveness_reasons=list(p.liveness_reasons or []),
            latest_review_days=p.latest_review_days,
            lat=p.lat,
            lng=p.lng,
            maps_url=p.maps_url,
            contact_status=p.contact_status,
            contact_note=p.contact_note,
            contact_at=p.contact_at,
            keywords=keywords or [],
            detail_scraped=p.detail_scraped,
            scraped_at=p.scraped_at,
            last_verified_at=p.last_verified_at,
        )
