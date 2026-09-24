"""`.env.example` phải khai đủ mọi biến của `Settings`.

VÌ SAO CẦN MỘT TEST CHO CHUYỆN NÀY — thiếu một biến ở đó KHÔNG gây lỗi ở đâu cả.
`Settings` có giá trị mặc định cho mọi trường, nên ứng dụng vẫn khởi động, test
vẫn xanh, Docker vẫn chạy. Thứ duy nhất hỏng là người dựng hệ thống trên máy khác
không biết biến đó tồn tại — họ không đặt được, và cũng không tắt được.

Đã xảy ra thật: `BCD_AI_JUDGE_ENABLED` và `BCD_AI_JUDGE_MAX_PER_QUERY` được thêm
vào `config.py` mà quên chép sang, nên tầng AI phân xử âm thầm gọi AI trên mọi
máy cài mới.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings

DUONG_DAN = Path(__file__).resolve().parent.parent / ".env.example"

# Biến KHÔNG thuộc `Settings` nhưng vẫn phải nằm trong file mẫu.
#
# Chúng do docker-compose và `scripts/backup_loop.sh` đọc, ứng dụng Python không
# bao giờ thấy. Liệt kê tường minh để test không báo động nhầm, và để ai thêm một
# biến kiểu này phải ghi tên nó vào đây — tức là phải nghĩ một lần về chuyện
# "cái này ai đọc".
NGOAI_SETTINGS = {
    "BCD_API_PORT",
    "BCD_DB_PORT",
    "BCD_WEB_PORT",
    "BCD_BACKUP_EVERY_HOURS",
    "BCD_BACKUP_KEEP",
}


def _da_khai() -> set[str]:
    noi_dung = DUONG_DAN.read_text(encoding="utf-8")
    return set(re.findall(r"^(BCD_[A-Z0-9_]+)=", noi_dung, re.MULTILINE))


def test_moi_bien_cua_settings_deu_co_trong_env_example():
    can = {f"BCD_{ten.upper()}" for ten in Settings.model_fields}
    thieu = sorted(can - _da_khai())
    assert not thieu, (
        "Thiếu trong .env.example: " + ", ".join(thieu) + ". "
        "Thêm biến vào config.py thì phải khai cả ở file mẫu, kèm một dòng nói "
        "nó làm gì — máy mới clone về chỉ đọc file đó."
    )


def test_khong_co_bien_thua_ma_khong_ai_doc():
    """Chiều ngược lại: biến trong file mẫu mà không ai đọc là chỉ dẫn sai.

    Người dựng hệ thống đặt nó, thấy không có tác dụng gì, rồi mất thời gian đi
    tìm xem mình sai ở đâu. Xoá một trường trong `config.py` mà quên dọn file mẫu
    là sinh ra đúng thứ đó.
    """
    can = {f"BCD_{ten.upper()}" for ten in Settings.model_fields}
    thua = sorted(_da_khai() - can - NGOAI_SETTINGS)
    assert not thua, (
        "Có trong .env.example nhưng không ai đọc: " + ", ".join(thua) + ". "
        "Hoặc xoá đi, hoặc thêm vào NGOAI_SETTINGS nếu nó do compose/script đọc."
    )
