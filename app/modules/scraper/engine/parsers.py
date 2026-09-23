"""Hàm bóc tách thuần — không I/O, không trình duyệt, nên test được offline.

Google Maps chạy được ở hai ngôn ngữ ta quan tâm (hl=vi, hl=en); mọi biểu thức
chính quy ở đây chấp nhận cả hai.
"""
from __future__ import annotations

import re
from urllib.parse import unquote

import phonenumbers
from phonenumbers import NumberParseException

NBSP = chr(0x00A0)
THIN_SPACE = chr(0x2009)
NARROW_NBSP = chr(0x202F)
MIDDLE_DOT = chr(0x00B7)
DOT_OPERATOR = chr(0x22C5)
BULLET_CHARS = MIDDLE_DOT + DOT_OPERATOR

_FEATURE_ID_RE = re.compile(r"!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)")
_LATLNG_DATA_RE = re.compile(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)")
_LATLNG_AT_RE = re.compile(r"/@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)")
_PLACE_NAME_RE = re.compile(r"/maps/place/([^/?#]+)")
_NUMBER_RE = re.compile(r"(\d+)(?:([.,])(\d+))?")
_REVIEW_COUNT_RE = re.compile(
    r"(\d[\d.,\s]*)\s*([KNM])?\s*(?:reviews?|bài đánh giá|đánh giá|lượt đánh giá)",
    re.IGNORECASE,
)
_PAREN_COUNT_RE = re.compile(r"\(\s*(\d[\d.,]*)\s*([KNM])?\s*\)")
_MULTIPLIER = {"K": 1_000, "N": 1_000, "M": 1_000_000}

# Tiền tố Google đặt trước giá trị thật trong aria-label, vd "Địa chỉ: 12 Lê Lợi".
LABEL_PREFIXES = {
    "address": ("Address:", "Địa chỉ:"),
    "phone": ("Phone:", "Điện thoại:", "Số điện thoại:"),
    "plus_code": ("Plus code:", "Mã Plus:", "Mã cộng:"),
    "website": ("Website:", "Trang web:"),
}

CLOSED_PERMANENTLY_MARKERS = ("permanently closed", "đã đóng cửa vĩnh viễn", "đóng cửa vĩnh viễn")
CLOSED_TEMPORARILY_MARKERS = ("temporarily closed", "tạm thời đóng cửa", "tạm đóng cửa")

_HOURS_SUMMARY_TAIL_RE = re.compile(
    r"\s*[" + BULLET_CHARS + r"]?\s*(Xem thêm giờ|See more hours|Show more hours|Ẩn giờ mở cửa)\s*$",
    re.IGNORECASE,
)
_OPEN_STATE_RE = re.compile(
    r"(đang mở cửa|đã đóng cửa|mở cả ngày|mở 24|đóng cửa vào|mở cửa lúc|tạm thời đóng cửa"
    r"|đóng cửa vĩnh viễn|open|closed|closes|opens|24 hours)",
    re.IGNORECASE,
)
# Cửa sàng lọc RẺ: đoạn phải TOÀN là ký tự của một số điện thoại. Chặn ngay mọi
# đoạn có chữ cái (địa chỉ, tên ngành nghề) trước khi gọi tới phonenumbers.
# KHÔNG ghim đầu số quốc gia ở đây — bản cũ viết `(?:\+?84|0)` nên mọi số nước
# ngoài dạng quốc tế (+66, +65, +1, +81...) đều bị loại, xem `is_phone_segment`.
_PHONE_SHAPE_RE = re.compile(r"^[+(]?[\d][\d\s.()+-]{7,22}$")
# Thẻ của địa điểm CHƯA CÓ ĐÁNH GIÁ NÀO không hiện sao, mà hiện thẳng câu này.
# Nó chiếm đúng vị trí mà bộ bóc tách coi là "danh mục", nên nếu không nhận ra
# thì "Chưa có bài đánh giá" đi thẳng vào cột Ngành nghề — đo thật: 426/723 dòng
# của một lần quét. Và tệ hơn: địa điểm mới mở (chưa ai đánh giá) là nhóm đáng
# quan tâm nhất với người bán hàng, lại bị mất luôn ngành nghề thật.
_NO_REVIEW_RE = re.compile(
    r"^(chưa có (bài )?đánh giá|không có bài đánh giá|no reviews?|be the first to review)",
    re.IGNORECASE,
)
_RATING_ONLY_RE = re.compile(r"^\d+[.,]\d+\s*(\([\d.,\sKNM]+\))?$")
_SPONSORED_RE = re.compile(r"(Được tài trợ|Sponsored|Quảng cáo)", re.IGNORECASE)

_REL_NUM_WORDS = {
    "một": 1, "mot": 1, "a": 1, "an": 1, "hai": 2, "ba": 3, "bốn": 4, "bon": 4,
    "năm": 5, "nam": 5, "sáu": 6, "sau": 6, "bảy": 7, "bay": 7, "tám": 8, "tam": 8,
    "chín": 9, "chin": 9, "mười": 10, "muoi": 10,
}
_REL_UNIT_DAYS = {
    "ngày": 1, "ngay": 1, "day": 1, "days": 1,
    "tuần": 7, "tuan": 7, "week": 7, "weeks": 7,
    "tháng": 30, "thang": 30, "month": 30, "months": 30,
    "năm": 365, "nam": 365, "year": 365, "years": 365,
}
# Hai lối diễn đạt đều gặp: "2 tuần trước" và "cách đây 2 tuần".
# Đuôi "trước"/"ago" chỉ bắt buộc khi KHÔNG có tiền tố "cách đây".
_REL_DATE_RE = re.compile(
    r"(?:(?P<prefix>cách đây)\s+)?"
    r"(?P<num>[0-9]+|[a-zàáâãèéêìíòóôõùúăđĩũơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]+)"
        # Nhánh DÀI đứng trước nhánh ngắn: "days" phải được thử trước "day", nếu không
    # regex dừng ở "day" rồi phần đuôi " ago" không còn khớp được nữa.
    r"\s*(?P<unit>ngày|ngay|tuần|tuan|tháng|thang|năm|nam|days|day|weeks|week|months|month|years|year)"
    r"(?:\s*(?P<suffix>trước|ago))?",
    re.IGNORECASE,
)


# Vùng ký tự RIÊNG TƯ của Unicode (U+E000-U+F8FF). Google nhúng glyph của bộ
# icon font ngay vào text của thẻ kết quả — `innerText` đọc được nó, còn trình
# duyệt thì vẽ ra một biểu tượng. Không lọc thì nó chui thẳng vào địa chỉ và
# hiện ra dưới dạng ô vuông, hoặc tệ hơn là một dấu phẩy cụt đầu dòng:
# ", 201" -> ", 201" (đã gặp thật trên dữ liệu Chiang Mai).
_PRIVATE_USE_RE = re.compile(r"[-]")


def normalize_ws(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = _PRIVATE_USE_RE.sub(" ", text)
    cleaned = re.sub("[" + NBSP + THIN_SPACE + NARROW_NBSP + "]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Sau khi bỏ icon, đoạn có thể chỉ còn lại dấu phân cách mồ côi (", 201" ->
    # ta muốn "201"). Gọt dấu phẩy/gạch ở hai đầu, nhưng KHÔNG gọt chữ số hay
    # chữ cái — "1366" là một địa chỉ ngắn thật, không phải rác.
    cleaned = cleaned.strip(" ,;-·")
    return cleaned or None


def parse_place_url(url: str) -> dict:
    """Lấy feature_id, cid, lat, lng và tên từ URL /maps/place.

    Ưu tiên cặp !3d/!4d (ghim của địa điểm) hơn @lat,lng (tâm khung nhìn).
    """
    out: dict = {"feature_id": None, "cid": None, "lat": None, "lng": None, "name": None}
    if not url:
        return out
    m = _FEATURE_ID_RE.search(url)
    if m:
        out["feature_id"] = m.group(1).lower()
        out["cid"] = feature_id_to_cid(out["feature_id"])
    m = _LATLNG_DATA_RE.search(url) or _LATLNG_AT_RE.search(url)
    if m:
        out["lat"], out["lng"] = float(m.group(1)), float(m.group(2))
    m = _PLACE_NAME_RE.search(url)
    if m:
        out["name"] = normalize_ws(unquote(m.group(1)).replace("+", " "))
    return out


def feature_id_to_cid(feature_id: str | None) -> str | None:
    """'0x3175...:0x1a2b' -> chuỗi thập phân của khối thứ hai (CID của Google)."""
    if not feature_id or ":" not in feature_id:
        return None
    try:
        return str(int(feature_id.split(":", 1)[1], 16))
    except ValueError:
        return None


def strip_label(value: str | None, kind: str) -> str | None:
    """Bỏ tiền tố kiểu 'Địa chỉ:' khỏi aria-label."""
    value = normalize_ws(value)
    if not value:
        return None
    for prefix in LABEL_PREFIXES.get(kind, ()):
        if value.lower().startswith(prefix.lower()):
            return normalize_ws(value[len(prefix):])
    return value


def parse_rating(text: str | None) -> float | None:
    """'4,5 sao' / '4.5 stars' / '4,5' -> 4.5"""
    if not text:
        return None
    m = _NUMBER_RE.search(text)
    if not m:
        return None
    whole, sep, frac = m.group(1), m.group(2), m.group(3)
    if sep and frac and len(frac) == 3:
        # "1.234" / "1,234" là cách viết hàng nghìn (số lượt đánh giá), không phải
        # điểm 1,234 sao. Không chặn thì nó lọt thẳng vào thang 0-5.
        return None
    try:
        value = float(f"{whole}.{frac}") if sep and frac else float(whole)
    except ValueError:
        return None
    return value if 0 <= value <= 5 else None


def _to_int(num: str, suffix: str | None) -> int | None:
    num = num.strip()
    if suffix:
        try:
            return int(float(num.replace(",", ".")) * _MULTIPLIER[suffix.upper()])
        except ValueError:
            return None
    digits = re.sub(r"\D", "", num)
    return int(digits) if digits else None


def parse_review_count(text: str | None) -> int | None:
    """'1.234 bài đánh giá' / '1,234 reviews' / '(1.234)' / '1,2 N đánh giá' -> int"""
    if not text:
        return None
    m = _REVIEW_COUNT_RE.search(text) or _PAREN_COUNT_RE.search(text)
    if not m:
        return None
    return _to_int(m.group(1), m.group(2))


def parse_phone_item_id(item_id: str | None) -> str | None:
    """'phone:tel:+84901234567' -> '+84901234567'"""
    if not item_id:
        return None
    if item_id.startswith("phone:tel:"):
        return normalize_ws(unquote(item_id[len("phone:tel:"):])) or None
    return None


def detect_business_status(text: str | None) -> str:
    if not text:
        return "OPERATIONAL"
    lowered = text.lower()
    if any(m in lowered for m in CLOSED_PERMANENTLY_MARKERS):
        return "CLOSED_PERMANENTLY"
    if any(m in lowered for m in CLOSED_TEMPORARILY_MARKERS):
        return "CLOSED_TEMPORARILY"
    return "OPERATIONAL"


def parse_hours_summary(label: str | None) -> str | None:
    """'Đang mở cửa | Đóng cửa vào 22:00 | Xem thêm giờ' -> bỏ phần 'Xem thêm giờ'."""
    label = normalize_ws(label)
    if not label:
        return None
    label = _HOURS_SUMMARY_TAIL_RE.sub("", label)
    label = re.sub(r"\s*[" + BULLET_CHARS + r"]\s*", " " + MIDDLE_DOT + " ", label)
    return normalize_ws(label.strip(" " + BULLET_CHARS))


def parse_relative_days(text: str | None) -> int | None:
    """'2 năm trước' -> 730 · '3 tháng trước' -> 90 · 'a year ago' -> 365."""
    if not text:
        return None
    m = _REL_DATE_RE.search(text.lower())
    if not m:
        return None
    if not m.group("prefix") and not m.group("suffix"):
        # "giờ mở cửa 2 tuần" không phải mốc thời gian — cần ít nhất một dấu hiệu.
        return None
    raw_num, unit = m.group("num"), m.group("unit")
    if raw_num.isdigit():
        n = int(raw_num)
    else:
        n = _REL_NUM_WORDS.get(raw_num.strip())
        if n is None:
            return None
    days = _REL_UNIT_DAYS.get(unit.lower())
    return n * days if days else None


def newest_review_days(labels: list[str] | None) -> int | None:
    """Đánh giá mới nhất cách đây bao nhiêu ngày (nhỏ nhất trong danh sách)."""
    if not labels:
        return None
    values = [d for d in (parse_relative_days(t) for t in labels) if d is not None]
    return min(values) if values else None


def split_segments(line: str) -> list[str]:
    """Tách một dòng của thẻ kết quả theo dấu chấm giữa, bỏ đoạn rỗng."""
    parts = re.split("[" + BULLET_CHARS + "]", line or "")
    return [p for p in (normalize_ws(x) for x in parts) if p]


def is_phone_segment(segment: str, region: str = "VN") -> bool:
    """Đoạn text này có phải số điện thoại không, xét theo VÙNG đang quét.

    Trước đây việc này do một regex tự chế quyết định, và regex đó ghim cứng đầu
    số Việt Nam (`+84` hoặc `0`). Hệ quả đo được: `+66 2 281 9715` (Bangkok),
    `+65 6222 3333` (Singapore), `+1 718-555-1234` (New York) đều bị coi là
    KHÔNG phải số điện thoại. Thẻ kết quả nước ngoài vì thế mất sạch SĐT, và tệ
    hơn — ở thẻ chưa có dòng ngành nghề, đoạn SĐT rơi xuống nhánh gán
    `category`, nên số điện thoại chui vào cột "Ngành nghề" của file xuất.

    Giờ để `phonenumbers` quyết định: nó biết quy tắc của từng nước, nên vừa
    nhận đúng số nội địa không có số 0 dẫn (Singapore, Hong Kong), vừa loại được
    chuỗi số trong địa chỉ — "325 169-170" đủ 9 chữ số nhưng không phải số hợp lệ
    ở bất kỳ vùng nào, trong khi luật đếm chữ số cũ thì cho qua.
    """
    segment = (segment or "").strip()
    if not _PHONE_SHAPE_RE.match(segment):
        return False
    try:
        parsed = phonenumbers.parse(segment, (region or "VN").upper())
    except NumberParseException:
        return False
    return phonenumbers.is_valid_number(parsed)


def parse_card(
    name: str | None,
    lines: list[str] | None,
    rating_labels: list[str] | None = None,
    region: str = "VN",
) -> dict:
    """Bóc thẻ kết quả trong danh sách tìm kiếm.

    Thẻ cho sẵn tên, SĐT, danh mục, địa chỉ rút gọn, rating, trạng thái mở cửa —
    tức là 3 trong 4 trường nghiệp vụ mà KHÔNG cần mở trang chi tiết.
    Website thì thẻ không có, đó là lý do duy nhất phải mở trang chi tiết.
    """
    out: dict = {
        "category": None, "address_short": None, "phone_raw": None,
        "business_status": "OPERATIONAL", "hours_summary": None,
        "rating": None, "review_count": None, "has_hours": False, "sponsored": False,
    }
    lines = [normalize_ws(x) or "" for x in (lines or [])]
    lines = [x for x in lines if x]

    for label in rating_labels or []:
        if out["rating"] is None:
            out["rating"] = parse_rating(label)
        if out["review_count"] is None:
            out["review_count"] = parse_review_count(label)

    status_bits: list[str] = []
    for line in lines:
        if _SPONSORED_RE.search(line):
            out["sponsored"] = True
            continue
        if name and line == name:
            continue
        if _RATING_ONLY_RE.match(line):
            if out["rating"] is None:
                out["rating"] = parse_rating(line)
            if out["review_count"] is None:
                out["review_count"] = parse_review_count(line)
            continue

        segments = split_segments(line)
        if not segments:
            continue
        # "Chưa có bài đánh giá" là THÔNG TIN ĐÁNH GIÁ, không phải ngành nghề.
        # Bỏ đoạn đó ra khỏi danh sách rồi mới xét tiếp — phần còn lại của dòng
        # (nếu có) vẫn là danh mục/địa chỉ thật và phải được giữ.
        if any(_NO_REVIEW_RE.match(seg) for seg in segments):
            if out["review_count"] is None:
                out["review_count"] = 0
            segments = [seg for seg in segments if not _NO_REVIEW_RE.match(seg)]
            if not segments:
                continue
        phones = [s for s in segments if is_phone_segment(s, region)]
        states = [s for s in segments if _OPEN_STATE_RE.search(s)]
        if phones or states:
            if phones and not out["phone_raw"]:
                out["phone_raw"] = phones[0]
            status_bits.extend(states)
            continue
        # Dòng còn lại là "danh mục · địa chỉ rút gọn"
        if out["category"] is None:
            out["category"] = segments[0]
            rest = segments[1:]
            if rest:
                out["address_short"] = ", ".join(rest)
        elif out["address_short"] is None and len(segments) > 1:
            out["address_short"] = ", ".join(segments[1:])

    if status_bits:
        joined = (" " + MIDDLE_DOT + " ").join(dict.fromkeys(status_bits))
        out["hours_summary"] = joined
        out["business_status"] = detect_business_status(joined)
        out["has_hours"] = out["business_status"] == "OPERATIONAL"
    return out
