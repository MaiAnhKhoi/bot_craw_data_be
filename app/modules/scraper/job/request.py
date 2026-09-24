from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class JobCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="Tên job cho dễ nhận ra")
    keywords: list[str] = Field(min_length=1, description="Mỗi dòng một từ khoá")
    locations: list[str] = Field(default_factory=list, description="Nhân tổ hợp với từng từ khoá")
    hl: str = Field("vi", max_length=8, description="Ngôn ngữ giao diện Google Maps")
    gl: str = Field("vn", max_length=8, description="Quốc gia ưu tiên khi tìm")
    region: str = Field("VN", max_length=4, description="Vùng dùng để chuẩn hoá số điện thoại")
    max_results_per_query: int = Field(200, ge=1, le=500)
    detail_mode: str = Field(
        "missing_only",
        description=(
            "always = luôn mở trang chi tiết; "
            "missing_only = chỉ mở khi còn thiếu trường cần (mặc định); "
            "never = chỉ đọc thẻ kết quả, nhanh nhất nhưng KHÔNG có website"
        ),
    )
    enrich_website: bool = Field(True, description="Lấy website doanh nghiệp và kiểm tra còn sống không")
    keyword_map: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Từ khoá riêng theo mã quốc gia, ví dụ {'TH': ['fruit wholesaler']}. "
            "Địa điểm thuộc quốc gia nào thì dùng từ khoá của quốc gia đó; "
            "không khai thì dùng `keywords` chung."
        ),
    )
    category_map: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Danh mục ngành nghề ĐƯỢC PHÉP GIỮ, theo mã quốc gia. Thẻ kết quả có "
            "ngành nghề ngoài danh mục bị loại NGAY lúc ghi, không vào bảng địa "
            "điểm (vẫn lưu vết ở `place_rejects` để soi lại). "
            "Không khai thì server tự tra từ bản dịch đã lưu của chính bộ từ khoá "
            "này; vẫn không có thì KHÔNG lọc gì cho quốc gia đó."
        ),
    )
    require_phone: bool = Field(
        True,
        description=(
            "Địa điểm không có số điện thoại thì KHÔNG giữ lại. "
            "Kiểm SAU pha chi tiết chứ không phải lúc đọc thẻ: đo thật thì 119/203 "
            "địa điểm chỉ lộ số sau khi mở trang chi tiết, chặn sớm là vứt oan chúng. "
            "Riêng chế độ `detail_mode=never` không mở trang nào nên chặn ngay ở thẻ."
        ),
    )
    ttl_days: int = Field(90, ge=0, le=3650, description="Bỏ qua địa điểm đã quét trong ngần này ngày")
    skip_recent_queries: bool = Field(
        True,
        description=(
            "Bỏ qua luôn cả TRUY VẤN đã chạy xong trong `ttl_days` ngày gần đây. "
            "`ttl_days` một mình chỉ tiết kiệm ở pha chi tiết — pha tìm kiếm vẫn "
            "cuộn lại toàn bộ danh sách để rồi nhận ra mọi địa điểm đều đã có."
        ),
    )

    @field_validator("keywords", "locations", mode="before")
    @classmethod
    def _clean_lines(cls, v):  # noqa: ANN001, ANN206
        if isinstance(v, str):
            v = v.splitlines()
        if not isinstance(v, list):
            return v
        out: list[str] = []
        for item in v:
            s = str(item).strip()
            if s and not s.startswith("#") and s not in out:
                out.append(s)
        return out

    @field_validator("detail_mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"always", "missing_only", "never"}:
            raise ValueError("detail_mode phải là always | missing_only | never")
        return v


class RemainingSplitRequest(BaseModel):
    """Sinh một job mới từ các dòng đang chọn ở trang "Địa bàn còn sót".

    Chỉ nhận CHUỖI TRUY VẤN, không nhận `stop_reason`: việc phải làm với mỗi dòng
    do server tự tra lại từ lần quét gần nhất. Giao diện có thể đã mở từ sáng,
    còn quyết định "chia nhỏ hay chạy lại" thì không được dựa trên dữ liệu cũ.
    """

    queries: list[str] = Field(
        min_length=1,
        max_length=500,
        description="Chuỗi truy vấn của các dòng đang chọn",
    )
    name: str | None = Field(
        None, max_length=200, description="Tên job mới; bỏ trống thì đặt theo ngày giờ"
    )
    max_results_per_query: int = Field(
        200,
        ge=1,
        le=500,
        description=(
            "Trần kết quả của job mới. Dòng dừng vì 'cap' chỉ cần đúng con số này "
            "cao hơn lần trước là đủ, không phải chia nhỏ địa bàn."
        ),
    )

    @field_validator("queries", mode="before")
    @classmethod
    def _clean_queries(cls, v):  # noqa: ANN001, ANN206
        if not isinstance(v, list):
            return v
        out: list[str] = []
        for item in v:
            s = str(item).strip()
            if s and s not in out:
                out.append(s)
        return out
