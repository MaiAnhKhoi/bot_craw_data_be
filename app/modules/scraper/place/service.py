from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy.orm import Session
from unidecode import unidecode

from app.core.exceptions import NotFoundError
from app.core.pagination import Page, PageParams
from app.modules.scraper.engine.liveness import LivenessInput, evaluate
from app.modules.scraper.place.entity import PLACE_PENDING, Place
from app.modules.scraper.place.repository import PlaceFilter, PlaceRepository
from app.modules.scraper.place.response import PlaceResponse


def fold_text(value: str | None) -> str:
    """Bỏ dấu + viết thường + gom khoảng trắng, để 'quan 1' tìm ra 'Quận 1'."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", unidecode(value).lower()).strip()


def build_search_text(name: str | None, address: str | None, category: str | None = None) -> str:
    return fold_text(" ".join(x for x in (name, address, category) if x))


def recompute_liveness(place: Place) -> None:
    """Chấm lại điểm sống/chết từ các tín hiệu đang có trên bản ghi."""
    result = evaluate(
        LivenessInput(
            business_status=place.business_status,
            phone_e164=place.phone_e164,
            phone_valid=place.phone_valid,
            website=place.website,
            website_status=place.website_status,
            review_count=place.review_count,
            latest_review_days=place.latest_review_days,
            has_hours=place.has_hours,
        )
    )
    place.liveness_score = result.score
    place.liveness_label = result.label
    place.liveness_reasons = result.reasons


class PlaceService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = PlaceRepository(db)

    def list(self, f: PlaceFilter, params: PageParams) -> Page[PlaceResponse]:
        total = self.repo.count(f)
        rows = self.repo.page(f, params) if total else []
        kw = self.repo.keywords_for(p.id for p in rows)
        return Page.build([PlaceResponse.of(p, kw.get(p.id, [])) for p in rows], params, total)

    def get(self, place_id: int) -> PlaceResponse:
        place = self.repo.by_id(place_id)
        if place is None:
            raise NotFoundError(f"Không tìm thấy địa điểm {place_id}")
        kw = self.repo.keywords_for([place.id])
        return PlaceResponse.of(place, kw.get(place.id, []))

    def reverify(self, place_id: int) -> PlaceResponse:
        """Đặt lại hàng đợi để worker quét lại địa điểm này.

        Dùng khi nghi ngờ dữ liệu đã cũ — ví dụ sale gọi không được và muốn biết
        Google đã cập nhật trạng thái đóng cửa hay chưa.
        """
        place = self.repo.by_id(place_id)
        if place is None:
            raise NotFoundError(f"Không tìm thấy địa điểm {place_id}")
        place.status = PLACE_PENDING
        place.attempts = 0
        place.last_error = None
        place.last_verified_at = datetime.now(UTC)
        self.db.commit()
        self.db.refresh(place)
        kw = self.repo.keywords_for([place.id])
        return PlaceResponse.of(place, kw.get(place.id, []))
