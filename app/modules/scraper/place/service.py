from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import AppError, NotFoundError
from app.core.pagination import Page, PageParams
from app.core.text import fold_text
from app.modules.scraper.engine.liveness import LivenessInput, evaluate
from app.modules.scraper.place.entity import CONTACT_STATUSES, PLACE_PENDING, Place
from app.modules.scraper.place.repository import PlaceFilter, PlaceRepository
from app.modules.scraper.place.response import (
    CountryCountResponse,
    PlaceResponse,
    QueryCountResponse,
)

# `fold_text` sống ở app/core/text.py vì module geo cũng dùng chung; giữ lại tên
# ở đây để các chỗ đang import từ service không phải sửa.
__all__ = ["PlaceService", "build_search_text", "fold_text", "recompute_liveness", "sort_countries"]


def build_search_text(name: str | None, address: str | None, category: str | None = None) -> str:
    return fold_text(" ".join(x for x in (name, address, category) if x))


def sort_countries(items: list[CountryCountResponse]) -> list[CountryCountResponse]:
    """Nước có nhiều dữ liệu nhất lên đầu — đó là nước người dùng chọn hằng ngày,
    không nên bắt họ cuộn tìm.

    Bằng nhau thì xếp theo tên, và so tên ở dạng ĐÃ BỎ DẤU: xếp theo mã Unicode
    thô sẽ đẩy "Ấn Độ" xuống dưới tận "Zimbabwe" vì "Ấ" nằm sau "Z".
    """
    return sorted(items, key=lambda c: (-c.count, fold_text(c.name)))


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

    def queries(self) -> list[QueryCountResponse]:
        """Mọi lượt tìm đã sinh ra dữ liệu. Repository đã sắp sẵn theo số địa
        điểm giảm dần — lượt thu được nhiều nhất là lượt hay được soi nhất."""
        return [QueryCountResponse(query=q, count=n) for q, n in self.repo.query_counts()]

    def countries(self) -> list[CountryCountResponse]:
        return sort_countries(
            [CountryCountResponse.of(code, n) for code, n in self.repo.country_counts()]
        )

    def get(self, place_id: int) -> PlaceResponse:
        place = self.repo.by_id(place_id)
        if place is None:
            raise NotFoundError(f"Không tìm thấy địa điểm {place_id}")
        kw = self.repo.keywords_for([place.id])
        return PlaceResponse.of(place, kw.get(place.id, []))

    def set_contact(
        self, place_id: int, status: str, note: str | None
    ) -> PlaceResponse:
        """Ghi lại việc bên mình đã liên hệ tới đâu.

        `contact_at` chỉ đặt khi trạng thái ĐỔI, không phải mỗi lần sửa ghi chú:
        nó có nghĩa "lần cuối trạng thái thay đổi", sửa lỗi chính tả trong ghi
        chú mà làm mốc nhảy lên thì con số đó hết dùng được để lọc.
        """
        from datetime import UTC, datetime

        if status not in CONTACT_STATUSES:
            raise AppError(
                f"Trạng thái chăm sóc không hợp lệ: {status!r}",
                code="VALIDATION_ERROR",
                status_code=422,
            )
        place = self.repo.by_id(place_id)
        if place is None:
            raise NotFoundError(f"Không tìm thấy địa điểm {place_id}")
        if place.contact_status != status:
            place.contact_status = status
            place.contact_at = datetime.now(UTC)
        # Chuỗi rỗng = xoá ghi chú; None = giữ nguyên ghi chú đang có.
        if note is not None:
            place.contact_note = note.strip() or None
        self.db.commit()
        self.db.refresh(place)
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
        # CỐ Ý không đụng `last_verified_at`. Nó có nghĩa là "lần cuối đã XÁC MINH
        # địa điểm này còn sống", mà ở đây chưa có request nào tới Google cả —
        # mới chỉ xếp hàng. Ghi mốc ở đây là tự nói dối: bản ghi trông như vừa
        # được kiểm tra trong khi thực tế chưa, và nó lên thẳng API lẫn file xuất.
        # Worker sẽ đặt mốc thật sau khi quét xong (xem `apply_detail`).
        self.db.commit()
        self.db.refresh(place)
        kw = self.repo.keywords_for([place.id])
        return PlaceResponse.of(place, kw.get(place.id, []))
