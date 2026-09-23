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


# ----- vì sao vòng cuộn dừng lại -----
#
# Ba lý do dưới đây trông giống hệt nhau nếu chỉ nhìn số kết quả, nhưng dẫn tới
# hai quyết định trái ngược: "đã quét sạch địa bàn" hay "còn sót, phải chia nhỏ".
# Một tỉnh ra 95 kết quả rồi thấy dòng "Bạn đã xem hết danh sách" là XONG; tỉnh
# khác ra 95 rồi bị Google ngắt là CÒN SÓT cả nghìn. Không lưu lại thì người dùng
# chỉ còn cách đoán mò theo con số.
STOP_EXHAUSTED = "exhausted"   # Google báo hết danh sách -> đã quét sạch
STOP_CUT_OFF = "cut_off"       # cuộn mãi không ra thẻ mới -> Google ngắt, CÒN SÓT
STOP_CAP = "cap"               # chạm trần `max_results_per_query` của chính mình
STOP_EMPTY = "empty"           # không có kết quả nào
STOP_UNKNOWN = "unknown"       # danh sách không hiện ra, không kết luận được
# Không phải lý do của vòng cuộn: truy vấn còn chưa được chạy. Đặt chung bảng vì
# trên giao diện nó trả lời đúng cùng một câu hỏi "vì sao truy vấn này dừng".
STOP_RECENT = "recent"         # bỏ qua vì chính truy vấn này vừa chạy xong gần đây


@dataclass
class SearchOutcome:
    """Kết quả một truy vấn: các thẻ đọc được VÀ vì sao ngừng cuộn."""

    cards: list[CardResult] = field(default_factory=list)
    stop_reason: str = STOP_UNKNOWN

    def __len__(self) -> int:
        return len(self.cards)

    def __iter__(self):  # noqa: ANN204 — cho phép `for card in outcome`
        return iter(self.cards)


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
