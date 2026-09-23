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


class KeywordSet(Base):
    """Một BỘ TỪ KHOÁ có tên, để gọi lại chính xác mà không phải gõ tay.

    Vì sao cần, khi đã có `KeywordTranslation` làm bộ nhớ đệm: khoá đệm là hàm
    băm của chính bộ từ khoá. Nó bền với đảo thứ tự, hoa/thường và khoảng trắng
    thừa — nhưng THIẾU MỘT TỪ hoặc SAI MỘT CHỮ là ra một khoá khác, và AI bị gọi
    lại từ đầu cho MỌI nước.

    Người dùng không có cách nào gõ lại chính xác một bộ mười từ khoá tiếng Việt
    sau vài tuần. Bảng này giữ nguyên văn bộ đó, nên chọn lại là trúng đệm 100%.
    Nó KHÔNG lưu bản dịch — bản dịch vẫn nằm ở `keyword_translations`, nối với
    nhau qua `source_hash`.
    """

    __tablename__ = "keyword_sets"
    __table_args__ = (UniqueConstraint("name_key", name="uq_keyword_sets_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # Tên đã chuẩn hoá (thường + gọn khoảng trắng) để so trùng. Lưu riêng thay vì
    # so bằng `lower(name)` lúc truy vấn: có cột thì ràng buộc UNIQUE mới chặn
    # được "Trái cây" và "trái cây" cùng tồn tại.
    name_key: Mapped[str] = mapped_column(String(200), index=True)
    keywords: Mapped[list] = mapped_column(JSONB, default=list)
    # Cầu nối sang `keyword_translations`. Lưu sẵn thay vì tính lại mỗi lần đọc,
    # để câu đếm "đã dịch bao nhiêu nước" chỉ là một phép JOIN.
    source_hash: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
