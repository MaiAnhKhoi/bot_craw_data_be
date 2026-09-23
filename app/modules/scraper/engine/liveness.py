"""Chấm điểm "công ty còn hoạt động hay không".

Bài toán: danh bạ Google Maps đầy những doanh nghiệp đã nghỉ từ lâu nhưng hồ sơ
vẫn còn. Gọi điện vào đó là tốn công sale. Ta gom nhiều tín hiệu rẻ tiền lại
thành một điểm số để sale biết nên gọi ai trước.

Nguyên tắc:
- Tín hiệu do Google KHẲNG ĐỊNH (đã đóng cửa vĩnh viễn) thì quyết định luôn.
- Các tín hiệu còn lại chỉ là SUY ĐOÁN nên chỉ trừ điểm, không kết luận một mình.
- Mọi lý do trừ điểm đều trả về cho giao diện để người dùng tự phán đoán,
  không để hộp đen quyết thay.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# mã lý do -> (điểm trừ, mô tả cho tài liệu; FE tự dịch sang tiếng Việt)
PENALTIES: dict[str, int] = {
    "CLOSED_PERMANENTLY": 100,   # Google khẳng định đã đóng cửa vĩnh viễn
    "CLOSED_TEMPORARILY": 45,
    "NO_PHONE": 15,
    "INVALID_PHONE": 10,
    "NO_WEBSITE": 5,
    "WEBSITE_DEAD": 25,
    "WEBSITE_PARKED": 20,
    "NO_REVIEWS": 20,
    "FEW_REVIEWS": 8,
    "REVIEWS_STALE_1Y": 6,
    "REVIEWS_STALE_2Y": 16,
    "REVIEWS_STALE_3Y": 28,
    "NO_HOURS": 10,
}

ACTIVE_THRESHOLD = 70
SUSPECT_THRESHOLD = 40


@dataclass
class LivenessInput:
    business_status: str = "OPERATIONAL"
    phone_e164: str | None = None
    phone_valid: bool | None = None
    website: str | None = None
    website_status: str = "NONE"        # OK | DEAD | PARKED | UNCHECKED | NONE
    review_count: int | None = None
    latest_review_days: int | None = None
    has_hours: bool = False


@dataclass
class LivenessResult:
    score: int
    label: str
    reasons: list[str] = field(default_factory=list)


def evaluate(data: LivenessInput) -> LivenessResult:
    reasons: list[str] = []

    if data.business_status == "CLOSED_PERMANENTLY":
        # Không cần xét gì thêm: đây là sự thật do Google ghi nhận.
        return LivenessResult(score=0, label="DEAD", reasons=["CLOSED_PERMANENTLY"])
    if data.business_status == "CLOSED_TEMPORARILY":
        reasons.append("CLOSED_TEMPORARILY")

    if not data.phone_e164:
        reasons.append("NO_PHONE")
    elif data.phone_valid is False:
        reasons.append("INVALID_PHONE")

    if not data.website:
        reasons.append("NO_WEBSITE")
    elif data.website_status == "DEAD":
        reasons.append("WEBSITE_DEAD")
    elif data.website_status == "PARKED":
        reasons.append("WEBSITE_PARKED")

    if data.review_count is None or data.review_count == 0:
        reasons.append("NO_REVIEWS")
    elif data.review_count < 3:
        reasons.append("FEW_REVIEWS")

    # Đánh giá cũ là tín hiệu mạnh nhất sau cờ của Google: quán còn bán thì
    # khách vẫn để lại đánh giá. Chỉ xét khi thật sự có đánh giá.
    days = data.latest_review_days
    if days is not None and (data.review_count or 0) > 0:
        if days >= 365 * 3:
            reasons.append("REVIEWS_STALE_3Y")
        elif days >= 365 * 2:
            reasons.append("REVIEWS_STALE_2Y")
        elif days >= 365:
            reasons.append("REVIEWS_STALE_1Y")

    if not data.has_hours:
        reasons.append("NO_HOURS")

    score = 100 - sum(PENALTIES.get(r, 0) for r in reasons)
    score = max(0, min(100, score))

    if score >= ACTIVE_THRESHOLD:
        label = "ACTIVE"
    elif score >= SUSPECT_THRESHOLD:
        label = "SUSPECT"
    else:
        label = "DEAD"
    return LivenessResult(score=score, label=label, reasons=reasons)
