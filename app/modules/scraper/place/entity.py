from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

PLACE_PENDING = "pending"
PLACE_DONE = "done"
PLACE_FAILED = "failed"

BUSINESS_OPERATIONAL = "OPERATIONAL"
BUSINESS_CLOSED_TEMP = "CLOSED_TEMPORARILY"
BUSINESS_CLOSED_PERM = "CLOSED_PERMANENTLY"


class Place(Base):
    """Một địa điểm duy nhất, khoá theo `feature_id` của Google.

    Bốn trường nghiệp vụ bắt buộc: name, address, phone, website.
    Các trường còn lại nuôi bộ chấm điểm còn hoạt động hay không (liveness).
    """

    __tablename__ = "places"
    __table_args__ = (
        Index("ix_places_liveness", "liveness_label"),
        Index("ix_places_status", "status"),
        Index("ix_places_phone_e164", "phone_e164"),
        Index("ix_places_scraped_at", "scraped_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # --- định danh ---
    feature_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    cid: Mapped[str | None] = mapped_column(String(32))
    maps_url: Mapped[str | None] = mapped_column(Text)

    # --- 4 trường nghiệp vụ ---
    name: Mapped[str] = mapped_column(String(300))
    address: Mapped[str | None] = mapped_column(Text)            # vị trí
    # Mã quốc gia ISO alpha-2. Hai việc phụ thuộc vào nó:
    #  1. Hiện cột "Quốc gia" — địa chỉ bị cắt trong bảng nên nhìn không ra nước nào.
    #  2. Vùng để phân tích số điện thoại. Đây mới là chỗ quan trọng: cùng một
    #     chuỗi "02 281 9715" đọc theo VN ra +8422819715 (sai, số rác), đọc theo
    #     TH ra +6622819715 (đúng). Xem `writer.region_of`.
    country_code: Mapped[str | None] = mapped_column(String(2), index=True)
    # NGUỒN đã xác định ra `country_code`: 'address' | 'coords' | 'gl' | NULL.
    # Tồn tại để biến một PHỎNG ĐOÁN VÔ HÌNH thành phỏng đoán nhìn thấy được.
    # 'gl' nghĩa là "đoán theo nước đang tìm" — yếu nhất, và cũng là dòng đáng
    # soi lại nhất vì quốc gia sai kéo theo số điện thoại nội địa đọc sai vùng,
    # mà số sai đó vẫn qua được mọi bộ kiểm tra.
    country_source: Mapped[str | None] = mapped_column(String(8), index=True)
    phone_raw: Mapped[str | None] = mapped_column(String(64))
    phone_e164: Mapped[str | None] = mapped_column(String(32))
    phone_national: Mapped[str | None] = mapped_column(String(32))
    phone_valid: Mapped[bool | None] = mapped_column(Boolean)
    website: Mapped[str | None] = mapped_column(Text)

    # --- tín hiệu chấm sống/chết ---
    category: Mapped[str | None] = mapped_column(String(160))
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    latest_review_days: Mapped[int | None] = mapped_column(Integer)
    business_status: Mapped[str] = mapped_column(String(24), default=BUSINESS_OPERATIONAL, index=True)
    hours_summary: Mapped[str | None] = mapped_column(String(160))
    has_hours: Mapped[bool] = mapped_column(Boolean, default=False)
    website_status: Mapped[str] = mapped_column(String(16), default="NONE")
    website_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    liveness_score: Mapped[int] = mapped_column(Integer, default=100)
    liveness_label: Mapped[str] = mapped_column(String(8), default="ACTIVE")
    liveness_reasons: Mapped[list] = mapped_column(JSONB, default=list)

    # --- vị trí địa lý ---
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)

    # Tên + địa chỉ đã bỏ dấu, viết thường — để tìm "quan 1" ra "Quận 1".
    # Có chỉ mục GIN trigram nên ILIKE '%...%' vẫn nhanh trên vài trăm nghìn dòng.
    search_text: Mapped[str | None] = mapped_column(Text)

    # --- vận hành ---
    status: Mapped[str] = mapped_column(String(12), default=PLACE_PENDING)
    detail_scraped: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    missing_fields: Mapped[list] = mapped_column(JSONB, default=list)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlaceKeyword(Base):
    """Từ khoá nào đã tìm ra địa điểm này (một địa điểm có thể đến từ nhiều từ khoá)."""

    __tablename__ = "place_keywords"
    __table_args__ = (UniqueConstraint("place_id", "keyword", name="uq_place_keywords"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"), index=True)
    keyword: Mapped[str] = mapped_column(String(300), index=True)


class JobPlace(Base):
    """Job nào đã chạm tới địa điểm này — cho phép lọc kết quả theo job."""

    __tablename__ = "job_places"
    __table_args__ = (UniqueConstraint("job_id", "place_id", name="uq_job_places"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("scrape_jobs.id", ondelete="CASCADE"), index=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"), index=True)
    is_new: Mapped[bool] = mapped_column(Boolean, default=True)
