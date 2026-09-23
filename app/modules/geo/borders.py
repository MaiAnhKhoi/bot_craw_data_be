"""Suy quốc gia từ TOẠ ĐỘ — offline, không thư viện hình học nào.

Vì sao cần, và vì sao nó mạnh hơn hai nguồn còn lại:

    địa chỉ  — chính xác khi Google ghi đầy đủ, nhưng thẻ kết quả thường chỉ cho
               địa chỉ RÚT GỌN ("90 Wichayanon Rd", "1366"): không có tên nước.
               Đo thật: 23% số dòng rơi vào ca này.
    gl       — chỉ nói ta đã TÌM ở nước nào, không nói địa điểm NẰM ở nước nào.
    toạ độ   — Google cho sẵn trong URL của MỌI thẻ (đo thật: 100% số dòng có),
               không phụ thuộc ngôn ngữ, không phụ thuộc cách viết địa chỉ.

Cái giá của việc đoán sai quốc gia không phải là một ô hiển thị sai, mà là SỐ
ĐIỆN THOẠI SAI: "081 882 1104" đọc theo vùng TH ra +66818821104, đọc theo vùng VN
ra +84818821104 — cả hai đều HỢP LỆ, nên không bộ kiểm tra nào bắt được và số sai
nằm im trong file xuất cho sale gọi.

Thuật toán: lọc thô bằng hộp bao, rồi bắn tia (even-odd). Không dùng shapely —
thêm một phụ thuộc nhị phân vào image worker chỉ để làm một phép kiểm tra 20 dòng
là không đáng, và bản thuần Python đã đủ nhanh (xem đo đạc ở `country_at`).

Dữ liệu: `data/borders.json`, sinh bằng `python -m scripts.build_country_borders`.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "borders.json"


@dataclass(frozen=True)
class Bien:
    code: str
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    rings: list[list[list[float]]]


@lru_cache(maxsize=1)
def load() -> list[Bien]:
    """Nạp một lần, giữ trong bộ nhớ suốt vòng đời tiến trình (~1,6 MB JSON).

    Danh sách đã được sắp theo DIỆN TÍCH HỘP BAO TĂNG DẦN ngay từ lúc sinh dữ
    liệu — thứ tự đó có ý nghĩa: nước nhỏ phải được xét TRƯỚC. Monaco nằm trọn
    trong hộp bao của Pháp, Lesotho nằm trong Nam Phi; xét nước lớn trước thì
    những nước đó không bao giờ khớp.
    """
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return [
        Bien(
            code=c["code"],
            min_x=c["bbox"][0],
            min_y=c["bbox"][1],
            max_x=c["bbox"][2],
            max_y=c["bbox"][3],
            rings=c["rings"],
        )
        for c in raw["countries"]
    ]


def _trong_vong(x: float, y: float, vong: list[list[float]]) -> bool:
    """Bắn tia theo luật chẵn-lẻ: đếm số lần tia ngang cắt cạnh đa giác.

    Lẻ = bên trong. Đây là thuật toán chuẩn, viết thẳng ra vì nó chỉ có bấy nhiêu.
    """
    ben_trong = False
    n = len(vong)
    j = n - 1
    for i in range(n):
        xi, yi = vong[i]
        xj, yj = vong[j]
        # Cạnh có bắc qua đường ngang y không, và giao điểm nằm bên phải x không.
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            ben_trong = not ben_trong
        j = i
    return ben_trong


# Bán kính chấp nhận khi điểm rơi RA NGOÀI mọi đa giác, tính theo độ (~11 km).
#
# Bắt buộc phải có. Bản đồ 1:50m vẽ đường bờ biển rất thô, mà doanh nghiệp thì
# tập trung ở đô thị ven biển — với ngành xuất nhập khẩu trái cây còn là ngay tại
# CẢNG. Đo thật: Manhattan (-74.006, 40.713) nằm ngoài đa giác đất liền của Mỹ ở
# tỉ lệ này. Không có lớp bù thì mọi địa điểm sát mép nước đều trả None rồi rơi
# về `gl`, đúng những nơi có nhiều lead nhất.
#
# 11 km đủ rộng để bù sai số vẽ bờ biển, vẫn đủ hẹp để một điểm giữa biển (dữ
# liệu hỏng) không bị gán bừa cho nước nào.
NGUONG_GAN_BO = 0.1


def _khoang_cach_toi_vong(x: float, y: float, vong: list[list[float]], he_so_x: float) -> float:
    """Khoảng cách ngắn nhất từ điểm tới các cạnh của đa giác, tính theo độ.

    Kinh độ được nhân `he_so_x = cos(vĩ độ)` để một độ kinh và một độ vĩ có cùng
    chiều dài thực. Bỏ bước này thì ở vĩ độ 60 khoảng cách theo chiều đông-tây bị
    thổi lên gấp đôi, và ngưỡng 11 km hoá ra 5,5 km ở Bắc Âu.
    """
    gan_nhat = float("inf")
    n = len(vong)
    for i in range(n):
        ax, ay = vong[i]
        bx, by = vong[(i + 1) % n]
        dx, dy = (bx - ax) * he_so_x, by - ay
        if dx == 0.0 and dy == 0.0:
            t = 0.0
        else:
            t = ((x - ax) * he_so_x * dx + (y - ay) * dy) / (dx * dx + dy * dy)
            t = max(0.0, min(1.0, t))
        cx, cy = ax + (bx - ax) * t, ay + (by - ay) * t
        d = math.hypot((x - cx) * he_so_x, y - cy)
        if d < gan_nhat:
            gan_nhat = d
            if gan_nhat == 0.0:
                return 0.0
    return gan_nhat


def country_at(lat: float | None, lng: float | None) -> str | None:
    """Mã ISO alpha-2 của quốc gia chứa toạ độ này, `None` nếu không nước nào chứa.

    Trả `None` cho điểm ngoài biển hoặc toạ độ rỗng — CỐ Ý không đoán nước gần
    nhất: một địa điểm ngoài khơi là dấu hiệu dữ liệu lạ, đoán bừa ở đó chỉ tạo
    ra một giá trị sai trông như thật.

    Chi phí đo thật trên máy phát triển:
        điểm trong đất liền  ~0,10 ms  (120 thẻ =  12 ms)
        điểm sát bờ biển     ~7,2  ms  (120 thẻ = 860 ms) — ca xấu nhất, phải đo
                                       khoảng cách tới 127 đa giác của Mỹ
        điểm giữa đại dương  ~0,03 ms  (hộp bao loại hết ngay)
    Ngay cả ca xấu nhất cũng dưới một giây cho cả truy vấn, trong khi chính truy
    vấn đó tốn 1-3 phút cuộn danh sách. Không đáng để tối ưu thêm.
    """
    if lat is None or lng is None:
        return None
    # Toạ độ vô lý (0,0 giữa Đại Tây Dương là giá trị mặc định kinh điển của dữ
    # liệu hỏng) vẫn đi qua đường bình thường và trả None, không cần chặn riêng.
    x, y = float(lng), float(lat)
    if not (-180.0 <= x <= 180.0 and -90.0 <= y <= 90.0):
        return None
    for bien in load():
        if not (bien.min_x <= x <= bien.max_x and bien.min_y <= y <= bien.max_y):
            continue
        for vong in bien.rings:
            if _trong_vong(x, y, vong):
                return bien.code

    # Không đa giác nào chứa điểm: gần như luôn là điểm sát mép nước bị đường bờ
    # biển thô đẩy ra ngoài. Lấy nước có bờ GẦN NHẤT, và chỉ khi còn trong ngưỡng.
    # Nhánh này CHỈ chạy cho điểm nằm ngoài đất liền, nên không ảnh hưởng tốc độ
    # của tuyệt đại đa số địa điểm.
    he_so_x = math.cos(math.radians(y)) or 1e-9
    gan_nhat, ma_gan_nhat = NGUONG_GAN_BO, None
    for bien in load():
        if not (
            bien.min_x - NGUONG_GAN_BO <= x <= bien.max_x + NGUONG_GAN_BO
            and bien.min_y - NGUONG_GAN_BO <= y <= bien.max_y + NGUONG_GAN_BO
        ):
            continue
        for vong in bien.rings:
            d = _khoang_cach_toi_vong(x, y, vong, he_so_x)
            if d < gan_nhat:
                gan_nhat, ma_gan_nhat = d, bien.code
    return ma_gan_nhat
