"""Lớp ghi dữ liệu của worker: thẻ kết quả / trang chi tiết -> bảng places.

Tách riêng khỏi `service.py` (phục vụ API đọc) vì đây là đường ghi, có luật khác:
idempotent theo `feature_id`, và mỗi địa điểm commit ngay để worker chết giữa
chừng vẫn không mất việc đã làm.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.scraper.engine.models import CardResult, DetailResult
from app.modules.scraper.engine.normalize import normalize_phone
from app.modules.scraper.place.entity import (
    BUSINESS_CLOSED_PERM,
    PLACE_DONE,
    PLACE_FAILED,
    PLACE_PENDING,
    JobPlace,
    Place,
    PlaceKeyword,
)
from app.modules.scraper.place.service import build_search_text, recompute_liveness


def _now() -> datetime:
    return datetime.now(UTC)


def needs_detail(place: Place, detail_mode: str, enrich_website: bool, ttl_days: int) -> bool:
    """Có phải mở trang chi tiết cho địa điểm này không.

    Đây là chỗ tiết kiệm thời gian lớn nhất của cả hệ thống, nên quyết định phải rõ:

    KHÔNG mở khi:
    1. Google đã khẳng định đóng cửa vĩnh viễn -> kết luận đã đủ, mở thêm vô ích.
    2. Chế độ `never` -> chỉ dùng những gì thẻ kết quả cho sẵn.
    3. Dữ liệu chi tiết còn trong hạn TTL -> vừa quét xong, chưa có gì đổi.

    CÓ mở khi đã hết hạn tươi và: chế độ `always`, hoặc còn thiếu trường nghiệp vụ,
    hoặc bản ghi đã quá hạn và cần xác minh lại xem công ty còn hoạt động không.
    """
    if place.business_status == BUSINESS_CLOSED_PERM:
        return False
    if detail_mode == "never":
        return False

    still_fresh = (
        place.detail_scraped
        and place.scraped_at is not None
        and ttl_days > 0
        and place.scraped_at > _now() - timedelta(days=ttl_days)
    )
    if still_fresh:
        return False
    if detail_mode == "always":
        return True

    # missing_only
    if not place.phone_e164 or not place.address:
        return True
    if not place.detail_scraped:
        # Website chỉ có trên trang chi tiết, thẻ kết quả không bao giờ có —
        # nên chỉ mở khi người dùng thật sự cần website.
        return enrich_website
    # Đã từng quét chi tiết nhưng đã quá hạn: mở lại để biết công ty còn sống không.
    # Thiếu bước này thì dữ liệu cũ sẽ nằm im vĩnh viễn và cả tính năng phát hiện
    # công ty ngừng hoạt động trở thành vô nghĩa.
    return True


class PlaceWriter:
    def __init__(self, db: Session, region: str = "VN") -> None:
        self.db = db
        self.region = region

    # ----- pha tìm kiếm -----
    def upsert_from_card(self, card: CardResult, job_id: int, keyword: str) -> tuple[Place, bool]:
        """Tạo mới hoặc làm tươi một địa điểm từ thẻ kết quả. Trả (place, có phải mới không)."""
        if not card.feature_id:
            # Không có định danh của Google thì không có cách khử trùng lặp đáng tin;
            # dùng URL làm khoá thay thế.
            key = card.maps_url.split("?", 1)[0]
        else:
            key = card.feature_id

        place = self.db.execute(select(Place).where(Place.feature_id == key)).scalar_one_or_none()
        is_new = place is None
        if place is None:
            place = Place(feature_id=key, name=card.name, status=PLACE_PENDING)
            self.db.add(place)

        place.name = place.name or card.name
        place.cid = place.cid or card.cid
        place.maps_url = card.maps_url or place.maps_url
        place.lat = place.lat if place.lat is not None else card.lat
        place.lng = place.lng if place.lng is not None else card.lng
        place.category = place.category or card.category
        # Địa chỉ từ thẻ là bản RÚT GỌN; chỉ dùng khi chưa có địa chỉ đầy đủ.
        if not place.address and card.address_short:
            place.address = card.address_short
        if card.rating is not None:
            place.rating = card.rating
        if card.review_count is not None:
            place.review_count = card.review_count
        if card.business_status != "OPERATIONAL" or not place.business_status:
            place.business_status = card.business_status
        if card.hours_summary:
            place.hours_summary = card.hours_summary
            place.has_hours = place.has_hours or card.has_hours
        if card.phone_raw and not place.phone_e164:
            place.phone_raw = card.phone_raw
            e164, national, valid = normalize_phone(card.phone_raw, self.region)
            place.phone_e164, place.phone_national, place.phone_valid = e164, national, valid

        place.last_seen_at = _now()
        place.search_text = build_search_text(place.name, place.address, place.category)
        recompute_liveness(place)
        self.db.flush()

        self._link_keyword(place.id, keyword)
        self._link_job(job_id, place.id, is_new)
        self.db.commit()
        return place, is_new

    def _link_keyword(self, place_id: int, keyword: str) -> None:
        exists = self.db.execute(
            select(PlaceKeyword.id).where(
                PlaceKeyword.place_id == place_id, PlaceKeyword.keyword == keyword
            )
        ).first()
        if not exists:
            self.db.add(PlaceKeyword(place_id=place_id, keyword=keyword))
            self.db.flush()

    def _link_job(self, job_id: int, place_id: int, is_new: bool) -> None:
        exists = self.db.execute(
            select(JobPlace.id).where(JobPlace.job_id == job_id, JobPlace.place_id == place_id)
        ).first()
        if not exists:
            self.db.add(JobPlace(job_id=job_id, place_id=place_id, is_new=is_new))
            self.db.flush()

    # ----- pha chi tiết -----
    def apply_detail(self, place: Place, detail: DetailResult) -> Place:
        if detail.name:
            place.name = detail.name
        if detail.address:
            place.address = detail.address          # địa chỉ đầy đủ đè bản rút gọn
        if detail.website:
            place.website = detail.website
            place.website_status = "UNCHECKED"
        if detail.category:
            place.category = detail.category
        if detail.rating is not None:
            place.rating = detail.rating
        if detail.review_count is not None:
            place.review_count = detail.review_count
        if detail.latest_review_days is not None:
            place.latest_review_days = detail.latest_review_days
        if detail.cid:
            place.cid = detail.cid
        if detail.lat is not None:
            place.lat = detail.lat
        if detail.lng is not None:
            place.lng = detail.lng
        place.business_status = detail.business_status
        place.hours_summary = detail.hours_summary or place.hours_summary
        place.has_hours = detail.has_hours or place.has_hours

        if detail.phone_raw:
            place.phone_raw = detail.phone_raw
            e164, national, valid = normalize_phone(detail.phone_raw, self.region)
            place.phone_e164, place.phone_national, place.phone_valid = e164, national, valid

        place.missing_fields = list(detail.missing_fields)
        place.detail_scraped = True
        place.status = PLACE_DONE
        place.attempts += 1
        place.last_error = None
        place.scraped_at = _now()
        place.last_verified_at = _now()
        place.search_text = build_search_text(place.name, place.address, place.category)
        recompute_liveness(place)
        self.db.commit()
        return place

    def finish_without_detail(self, place: Place) -> Place:
        """Kết thúc địa điểm chỉ với dữ liệu từ thẻ (không cần mở trang chi tiết)."""
        place.status = PLACE_DONE
        place.scraped_at = place.scraped_at or _now()
        place.last_verified_at = _now()
        if not place.website:
            place.website_status = "NONE"
        recompute_liveness(place)
        self.db.commit()
        return place

    def mark_failed(self, place: Place, error: str) -> None:
        place.status = PLACE_FAILED
        place.attempts += 1
        place.last_error = error[:500]
        self.db.commit()

    def apply_website_status(self, place: Place, status: str) -> None:
        place.website_status = status
        place.website_checked_at = _now()
        recompute_liveness(place)
        self.db.commit()

    # ----- truy vấn phục vụ worker -----
    def next_pending_of_job(self, job_id: int) -> Place | None:
        """Lấy MỘT địa điểm còn chờ xử lý.

        Vòng lặp pha chi tiết gọi hàm này mỗi lượt. Nạp cả danh sách (có thể hàng
        chục nghìn dòng) rồi chỉ dùng phần tử đầu sẽ biến vòng lặp thành O(n^2).
        """
        return (
            self.db.execute(
                select(Place)
                .join(JobPlace, JobPlace.place_id == Place.id)
                .where(JobPlace.job_id == job_id, Place.status == PLACE_PENDING)
                .order_by(Place.id)
                .limit(1)
            )
            .scalars()
            .first()
        )

    def count_places_of_job(self, job_id: int) -> int:
        return int(
            self.db.execute(
                select(func.count(JobPlace.place_id)).where(JobPlace.job_id == job_id)
            ).scalar_one()
        )

    def places_of_job(self, job_id: int, statuses: tuple[str, ...] = (PLACE_PENDING,)) -> list[Place]:
        return list(
            self.db.execute(
                select(Place)
                .join(JobPlace, JobPlace.place_id == Place.id)
                .where(JobPlace.job_id == job_id, Place.status.in_(statuses))
                .order_by(Place.id)
            )
            .scalars()
            .all()
        )

    def places_needing_website_check(self, job_id: int) -> list[Place]:
        return list(
            self.db.execute(
                select(Place)
                .join(JobPlace, JobPlace.place_id == Place.id)
                .where(
                    JobPlace.job_id == job_id,
                    Place.website.isnot(None),
                    Place.website_status.in_(("UNCHECKED", "NONE")),
                )
                .order_by(Place.id)
            )
            .scalars()
            .all()
        )
