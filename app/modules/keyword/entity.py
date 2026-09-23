from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class KeywordTranslation(Base):
    """Bộ nhớ đệm từ khoá bản địa do AI sinh.

    Khoá theo (bộ từ khoá gốc, quốc gia): cùng một bộ từ khoá hỏi lại lần hai là
    lấy từ đây, không tốn thêm lượt gọi AI. Dữ liệu này người dùng sửa được trên
    giao diện, và bản đã sửa cũng ghi đè vào đây.
    """

    __tablename__ = "keyword_translations"
    __table_args__ = (UniqueConstraint("source_hash", "country_code", name="uq_keyword_translations"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_hash: Mapped[str] = mapped_column(String(40), index=True)
    source_keywords: Mapped[list] = mapped_column(JSONB, default=list)
    country_code: Mapped[str] = mapped_column(String(4), index=True)
    language: Mapped[str] = mapped_column(String(8), default="en")
    keywords: Mapped[list] = mapped_column(JSONB, default=list)
    model: Mapped[str | None] = mapped_column(String(64))
    edited_by_user: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
