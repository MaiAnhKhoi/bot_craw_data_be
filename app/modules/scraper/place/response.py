from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.modules.scraper.place.entity import Place


class PlaceResponse(BaseModel):
    """4 trường nghiệp vụ đứng trước; phần còn lại phục vụ lọc và chấm sống/chết."""

    id: int
    name: str
    address: str | None          # vị trí
    phone: str | None            # dạng quốc gia, dễ đọc/bấm gọi
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
            phone=p.phone_national or p.phone_raw,
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
            keywords=keywords or [],
            detail_scraped=p.detail_scraped,
            scraped_at=p.scraped_at,
            last_verified_at=p.last_verified_at,
        )
